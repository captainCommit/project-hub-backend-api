"""Human-readable activity: who did it, what it was done to, and each change with IDs turned into names.

The raw old_values/new_values stay in the response; this adds actor_name, entity_name and changes.
"""

import re
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.account_member import AccountMember
from app.models.activity_log import ActivityLog
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.user import User

FIELD_LABELS = {
    "name": "Name",
    "title": "Title",
    "description": "Description",
    "body": "Comment",
    "start_date": "Start date",
    "finish_date": "Due date",
    "end_date": "End date",
    "due_date": "Due date",
    "percent_complete": "Progress",
    "duration_days": "Duration (days)",
    "story_points": "Story points",
    "status_id": "Status",
    "priority_id": "Priority",
    "task_type_id": "Type",
    "sprint_id": "Sprint",
    "parent_task_id": "Parent task",
    "predecessor_task_id": "Predecessor",
    "task_id": "Task",
    "project_id": "Project",
    "program_id": "Program",
    "portfolio_id": "Portfolio",
    "resource_id": "Resource",
    "user_id": "Person",
    "assigned_to": "Assigned to",
    "owner_id": "Owner",
    "allocated_hours": "Allocated hours",
    "weekly_capacity_hours": "Weekly capacity (hours)",
    "non_project_hours": "Non-project hours",
    "hourly_rate": "Hourly rate",
    "file_name": "File",
    "escalated_to": "Escalated to",
    "note": "Note",
}

# Bookkeeping that means nothing to a reader (the actor and time are shown on the entry itself).
HIDDEN_FIELDS = {
    "id",
    "account_id",
    "comment_id",
    "attachment_id",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
    "sort_order",
    "is_deleted",
    "deleted_at",
    "deleted_by",
}

PERCENT_FIELDS = {"percent_complete", "progress"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")
MAX_TEXT = 300


def field_label(field: str) -> str:
    if field in FIELD_LABELS:
        return FIELD_LABELS[field]
    base = field[:-3] if field.endswith("_id") else field
    return base.replace("_", " ").strip().capitalize() or field


def _as_uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and len(value) == 36:
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def _number(value: object) -> str | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    text = format(number.normalize(), "f")
    return text[:-2] if text.endswith(".0") else text


def _day(value: date) -> str:
    return f"{value:%b} {value.day}, {value.year}"


class ActivityDescriber:
    """Resolves the IDs in a batch of activity entries with one query per kind of record."""

    def __init__(self, db: Session, account_id: UUID) -> None:
        self.db = db
        self.account_id = account_id
        self.names: dict[UUID, str] = {}

    def describe(self, entries: Iterable[ActivityLog]) -> list[dict[str, Any]]:
        entries = list(entries)
        self.load_names(entries)
        return [self.describe_one(entry) for entry in entries]

    def describe_one(self, entry: ActivityLog) -> dict[str, Any]:
        return {
            "id": entry.id,
            "account_id": entry.account_id,
            "entity_type": entry.entity_type,
            "entity_id": entry.entity_id,
            "action": entry.action,
            "old_values": entry.old_values,
            "new_values": entry.new_values,
            "created_by": entry.created_by,
            "created_at": entry.created_at,
            "actor_name": self.actor_name(entry.created_by),
            "entity_name": self.names.get(entry.entity_id),
            "changes": self.changes(entry),
        }

    def actor_name(self, user_id: UUID | None) -> str | None:
        if user_id is None:
            return None
        return self.names.get(user_id) or "Former member"

    def changes(self, entry: ActivityLog) -> list[dict[str, Any]]:
        old = entry.old_values if isinstance(entry.old_values, dict) else {}
        new = entry.new_values if isinstance(entry.new_values, dict) else {}
        rows = []
        for field in dict.fromkeys([*old, *new]):
            if field in HIDDEN_FIELDS:
                continue
            old_text = self.format_value(field, old.get(field))
            new_text = self.format_value(field, new.get(field))
            if old_text is None and new_text is None:
                continue
            if field in old and field in new and old_text == new_text:
                continue
            rows.append({"field": field, "label": field_label(field), "old": old_text, "new": new_text})
        return rows

    def format_value(self, field: str, value: object) -> str | None:
        if value is None or value == "" or value == [] or value == {}:
            return None
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, list):
            parts = [self.format_value(field, item) for item in value]
            return ", ".join(part for part in parts if part) or None
        if isinstance(value, dict):
            label = value.get("label") or value.get("name") or value.get("title")
            return str(label) if label else None
        identifier = _as_uuid(value)
        if identifier is not None:
            return self.names.get(identifier, "(removed)")
        if field in PERCENT_FIELDS:
            number = _number(value)
            return f"{number}%" if number is not None else str(value)
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return _number(value)
        text = str(value)
        if DATE_RE.match(text):
            try:
                return _day(date.fromisoformat(text))
            except ValueError:
                return text
        if DATETIME_RE.match(text):
            try:
                moment = datetime.fromisoformat(text)
                return f"{_day(moment.date())}, {moment:%H:%M}" + (" UTC" if moment.utcoffset() is not None else "")
            except ValueError:
                return text
        if field.endswith(("_hours", "_rate", "_days", "_points")) and _number(text) is not None:
            return _number(text)
        text = " ".join(text.split())
        return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1].rstrip() + "…"

    # Name lookups ---------------------------------------------------------------------------------------

    def load_names(self, entries: list[ActivityLog]) -> None:
        ids: set[UUID] = set()
        for entry in entries:
            ids.add(entry.entity_id)
            if entry.created_by:
                ids.add(entry.created_by)
            for values in (entry.old_values, entry.new_values):
                if isinstance(values, dict):
                    for field, value in values.items():
                        if field not in HIDDEN_FIELDS:
                            ids.update(self._ids_in(value))
        if not ids:
            return
        self.load_users(ids)
        self.load_options(ids - self.names.keys())
        self.load_records(ids - self.names.keys())

    def _ids_in(self, value: object) -> set[UUID]:
        if isinstance(value, list):
            return {identifier for item in value for identifier in self._ids_in(item)}
        identifier = _as_uuid(value)
        return {identifier} if identifier else set()

    def load_users(self, ids: set[UUID]) -> None:
        # Only people in this account, so no one else's name leaks into the log.
        rows = self.db.execute(
            select(User.id, User.full_name, User.email)
            .join(AccountMember, AccountMember.user_id == User.id)
            .where(User.id.in_(ids), AccountMember.account_id == self.account_id)
        ).all()
        for user_id, full_name, email in rows:
            self.names[user_id] = full_name or email

    def load_options(self, ids: set[UUID]) -> None:
        if not ids:
            return
        rows = self.db.execute(
            select(OptionValue.id, OptionValue.label)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionValue.id.in_(ids),
                or_(OptionSet.account_id == self.account_id, OptionSet.account_id.is_(None)),
            )
        ).all()
        self.names.update({option_id: label for option_id, label in rows})

    def load_records(self, ids: set[UUID]) -> None:
        from app.services.activity import ACTIVITY_ENTITY_MODELS

        for entity_type, model in ACTIVITY_ENTITY_MODELS.items():
            if not ids:
                return
            if entity_type == "ACCOUNT" or not hasattr(model, "account_id"):
                continue
            columns = [model.id] + [
                getattr(model, attribute) for attribute in ("title", "name", "description") if hasattr(model, attribute)
            ]
            if len(columns) == 1:
                if entity_type == "COMMENT":
                    found = self.db.scalars(
                        select(model.id).where(model.id.in_(ids), model.account_id == self.account_id)
                    ).all()
                    self.names.update({record_id: "Comment" for record_id in found})
                    ids -= set(found)
                continue
            rows = self.db.execute(
                select(*columns).where(model.id.in_(ids), model.account_id == self.account_id)
            ).all()
            for record_id, *labels in rows:
                label = next((str(text) for text in labels if text), None)
                if label:
                    self.names[record_id] = self.format_value("name", label) or label
                    ids.discard(record_id)


def describe_activity(db: Session, account_id: UUID, entries: Iterable[ActivityLog]) -> list[dict[str, Any]]:
    return ActivityDescriber(db, account_id).describe(entries)
