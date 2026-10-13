"""Deciding what to send to Slack and Teams. Nothing is sent here; rows are added to the outbox (IntegrationMessage).

- Channel events come from the activity log, which every change in the app already goes through. The worker scans
  it each minute from where it last stopped (with a few minutes' overlap, made harmless by a unique constraint).
- Direct messages follow notifications: when a notification is created for someone who linked Slack or Teams and
  wants that kind as a direct message, a row is added in the same transaction.
- Overdue summaries go to channels once a day, at the digest hour of the person who set the channel up.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog
from app.models.integration import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationDestination,
    IntegrationMessage,
    IntegrationState,
    IntegrationUserLink,
)
from app.models.notification import Notification
from app.models.option_value import OptionValue
from app.models.project import Project
from app.services.activity import ACTIVITY_ENTITY_MODELS
from app.services.notification_preferences import NotificationPreferenceService, category_for, zone_for
from app.services.project_overview import HIGH_PRIORITY_KEYS
from app.services.raid import normalize_option_key

CURSOR_KEY = "activity_scan"
SCAN_OVERLAP = timedelta(minutes=5)
SCAN_BATCH = 2000
# On the very first run, start from now rather than replaying history into channels.
FIRST_SCAN_LOOKBACK = timedelta(minutes=1)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def status_changed(activity: ActivityLog) -> bool:
    old = activity.old_values if isinstance(activity.old_values, dict) else {}
    new = activity.new_values if isinstance(activity.new_values, dict) else {}
    return "status_id" in new and new.get("status_id") != old.get("status_id")


def is_high_priority(db: Session, priority_id: Any) -> bool:
    option = db.get(OptionValue, priority_id) if priority_id else None
    if option is None:
        return False
    return bool({normalize_option_key(option.value), normalize_option_key(option.label)} & HIGH_PRIORITY_KEYS)


def classify(db: Session, activity: ActivityLog) -> tuple[list[str], Project | None]:
    """The channel events an activity entry is, and the project it happened in."""
    model = ACTIVITY_ENTITY_MODELS.get(activity.entity_type)
    record: Any = db.get(model, activity.entity_id) if model is not None else None
    if record is None or getattr(record, "is_deleted", False):
        return [], None
    project_id = record.id if activity.entity_type == "PROJECT" else getattr(record, "project_id", None)
    project = db.get(Project, project_id) if project_id else None
    if project is None:
        return [], None

    kind, action = activity.entity_type, activity.action
    events: list[str] = []
    if action == "COMMENTED":
        events.append("comment_added")
    elif kind == "TASK" and action == "CREATED":
        events.append("task_created")
    elif kind == "TASK" and action in ("UPDATED", "TASK_BOARD_MOVED") and status_changed(activity):
        events.append("task_status_changed")
    elif kind == "RISK" and action == "CREATED":
        events.append("risk_created")
        if is_high_priority(db, record.priority_id):
            events.append("risk_created_high")
    elif kind == "ISSUE" and action == "CREATED":
        events.append("issue_created")
    elif kind == "DECISION" and action == "UPDATED" and status_changed(activity):
        events.append("decision_changed")
    elif kind == "PROJECT" and action == "UPDATED" and status_changed(activity):
        events.append("project_status_changed")
    return events, project


def covers(destination: IntegrationDestination, project: Project) -> bool:
    return (
        destination.project_id == project.id
        or destination.program_id == project.program_id
        or (destination.portfolio_id is not None and destination.portfolio_id == project.portfolio_id)
    )


def active_destinations(db: Session) -> list[IntegrationDestination]:
    return list(
        db.scalars(
            select(IntegrationDestination)
            .join(IntegrationConnection, IntegrationConnection.id == IntegrationDestination.connection_id)
            .where(IntegrationDestination.is_active.is_(True), IntegrationConnection.status == ConnectionStatus.ACTIVE.value)
        ).all()
    )


class EventScanner:
    def __init__(self, db: Session) -> None:
        self.db = db

    def scan(self, *, now: datetime | None = None) -> int:
        """Queue channel messages for activity since the last scan. Returns how many were queued."""
        now = now or datetime.now(UTC)
        state = self.db.get(IntegrationState, CURSOR_KEY)
        cursor = datetime.fromisoformat(state.value) if state else now - FIRST_SCAN_LOOKBACK
        destinations = active_destinations(self.db)
        queued = 0
        newest = cursor
        if destinations:
            rows = self.db.scalars(
                select(ActivityLog)
                .where(ActivityLog.created_at > cursor - SCAN_OVERLAP, ActivityLog.created_at <= now)
                .order_by(ActivityLog.created_at, ActivityLog.id)
                .limit(SCAN_BATCH)
            ).all()
            by_account: dict[UUID, list[IntegrationDestination]] = {}
            for destination in destinations:
                by_account.setdefault(destination.account_id, []).append(destination)
            already = self.already_queued([row.id for row in rows])
            for activity in rows:
                newest = max(newest, _aware(activity.created_at))
                candidates = by_account.get(activity.account_id)
                if not candidates:
                    continue
                events, project = classify(self.db, activity)
                if not events or project is None:
                    continue
                for destination in candidates:
                    event = next((e for e in events if e in (destination.events or [])), None)
                    if event is None or not covers(destination, project) or (destination.id, activity.id) in already:
                        continue
                    # The scan overlaps the previous one; never replay what happened before the channel was added.
                    if _aware(activity.created_at) < _aware(destination.created_at):
                        continue
                    self.db.add(
                        IntegrationMessage(
                            account_id=activity.account_id,
                            kind="ACTIVITY",
                            destination_id=destination.id,
                            activity_id=activity.id,
                            event=event,
                        )
                    )
                    already.add((destination.id, activity.id))
                    queued += 1
            # A full batch means more is waiting: continue from the last row read, not from now.
            if len(rows) < SCAN_BATCH:
                newest = max(newest, now)
        else:
            newest = now
        if state is None:
            self.db.add(IntegrationState(key=CURSOR_KEY, value=newest.isoformat()))
        else:
            state.value = newest.isoformat()
        self.db.commit()
        return queued

    def already_queued(self, activity_ids: list[UUID]) -> set[tuple[UUID, UUID]]:
        if not activity_ids:
            return set()
        rows = self.db.execute(
            select(IntegrationMessage.destination_id, IntegrationMessage.activity_id).where(
                IntegrationMessage.activity_id.in_(activity_ids)
            )
        ).all()
        return {(destination_id, activity_id) for destination_id, activity_id in rows if destination_id and activity_id}


def queue_direct_messages(db: Session, notification: Notification) -> int:
    """Called as a notification is created: queue it for the person's linked Slack and Teams, if they want it."""
    category = category_for(notification.notification_type)
    if category is None:
        return 0
    links = db.scalars(
        select(IntegrationUserLink)
        .join(IntegrationConnection, IntegrationConnection.id == IntegrationUserLink.connection_id)
        .where(
            IntegrationUserLink.account_id == notification.account_id,
            IntegrationUserLink.user_id == notification.user_id,
            IntegrationConnection.status == ConnectionStatus.ACTIVE.value,
        )
    ).all()
    queued = 0
    for link in links:
        if category in (link.dm_categories or []):
            db.add(
                IntegrationMessage(
                    account_id=notification.account_id,
                    kind="NOTIFICATION",
                    user_link_id=link.id,
                    notification_id=notification.id,
                )
            )
            queued += 1
    return queued


def queue_overdue_summaries(db: Session, *, now: datetime | None = None) -> int:
    """Hourly: queue each channel's daily overdue summary once its creator's digest hour has come."""
    now = now or datetime.now(UTC)
    preferences = NotificationPreferenceService(db)
    queued = 0
    for destination in active_destinations(db):
        if "task_overdue" not in (destination.events or []) or destination.created_by is None:
            continue
        preference = preferences.get_or_default(destination.created_by)
        local = now.astimezone(zone_for(preference))
        if local.hour < preference.digest_hour or destination.last_overdue_on == local.date():
            continue
        destination.last_overdue_on = local.date()
        db.add(IntegrationMessage(account_id=destination.account_id, kind="OVERDUE", destination_id=destination.id, event="task_overdue"))
        queued += 1
    db.commit()
    return queued
