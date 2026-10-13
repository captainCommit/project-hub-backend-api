"""Turning an outbox row into a ChatMessage, at send time, as the person it is checked against.

Returns None when the message should not go out any more: the item was deleted, or the person who set up the
channel (or the person a direct message is for) can no longer see it.
"""

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.account_member import AccountMember
from app.models.activity_log import ActivityLog
from app.models.integration import IntegrationDestination
from app.models.notification import Notification
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.user import User
from app.services.access import can_see_entity
from app.services.activity_display import describe_activity
from app.services.email_delivery import KIND_LABELS, describe_item
from app.services.tasks import TaskService
from app.services.integrations.messages import ChatMessage

OVERDUE_LIMIT = 15
COMMENT_CHARS = 300


def is_member(db: Session, account_id: UUID, user: User | None) -> bool:
    if user is None or not user.is_active:
        return False
    return db.query(AccountMember).filter_by(account_id=account_id, user_id=user.id).first() is not None


def scope_name(db: Session, destination: IntegrationDestination) -> str:
    for model, scope_id in ((Project, destination.project_id), (Program, destination.program_id), (Portfolio, destination.portfolio_id)):
        if scope_id:
            record = db.get(model, scope_id)
            return record.name if record else ""
    return ""


def render_activity(db: Session, activity: ActivityLog, event: str, viewer: User) -> ChatMessage | None:
    if not is_member(db, activity.account_id, viewer) or not can_see_entity(
        db, account_id=activity.account_id, user=viewer, entity_type=activity.entity_type, entity_id=activity.entity_id
    ):
        return None
    item = describe_item(db, activity.entity_type, activity.entity_id)
    if item is None:
        return None
    described = describe_activity(db, activity.account_id, [activity])[0]
    actor = described.get("actor_name") or "Someone"
    status = next((c for c in described.get("changes") or [] if c["field"] == "status_id"), None)
    record = _record(db, activity)
    project_id = activity.entity_id if activity.entity_type == "PROJECT" else getattr(record, "project_id", None)
    project = db.get(Project, project_id) if project_id else None
    facts: list[tuple[str, str]] = []
    if project is not None and activity.entity_type != "PROJECT":
        facts.append(("Project", project.name))
    lines: list[str] = []

    if event == "comment_added":
        title = f"{actor} commented on {item.kind.lower()} {item.label}"
        body = str((activity.new_values or {}).get("body") or "")
        if body:
            lines.append(body if len(body) <= COMMENT_CHARS else body[: COMMENT_CHARS - 1] + "…")
    elif event in ("task_status_changed", "decision_changed", "project_status_changed") and status:
        title = f"{item.label}: {status.get('old') or 'No status'} → {status.get('new') or 'No status'}"
        facts.append(("Changed by", actor))
    elif event == "task_created":
        title = f"New task: {item.label}"
        facts.append(("Added by", actor))
    elif event in ("risk_created", "risk_created_high", "issue_created"):
        title = f"New {item.kind.lower()}: {item.label}"
        priority = _option_label(db, getattr(record, "priority_id", None))
        if priority:
            facts.append(("Priority", priority))
        facts.append(("Raised by", actor))
    else:
        title = f"{item.kind} updated: {item.label}"
    return ChatMessage(title=title, link=item.path, lines=lines, facts=facts)


def _record(db: Session, activity: ActivityLog) -> object | None:
    from app.services.activity import ACTIVITY_ENTITY_MODELS

    model = ACTIVITY_ENTITY_MODELS.get(activity.entity_type)
    return db.get(model, activity.entity_id) if model is not None else None


def _option_label(db: Session, option_id: UUID | None) -> str | None:
    from app.models.option_value import OptionValue

    option = db.get(OptionValue, option_id) if option_id else None
    return option.label if option else None


def render_notification(db: Session, notification: Notification, user: User) -> ChatMessage | None:
    if not is_member(db, notification.account_id, user) or not can_see_entity(
        db, account_id=notification.account_id, user=user, entity_type=notification.entity_type, entity_id=notification.entity_id
    ):
        return None
    item = describe_item(db, notification.entity_type, notification.entity_id)
    account = db.get(Account, notification.account_id)
    lines = [notification.message] if notification.message else []
    return ChatMessage(
        title=notification.title,
        link=item.path if item else None,
        lines=lines,
        context=f"{KIND_LABELS.get(notification.entity_type.upper(), 'Item')} in {account.name if account else 'Project Hub'}",
    )


def render_overdue(db: Session, destination: IntegrationDestination, viewer: User, *, today: date | None = None) -> ChatMessage | None:
    """Overdue tasks in the channel's portfolio, program or project that its creator can see. None if there are none."""
    if not is_member(db, destination.account_id, viewer):
        return None
    filters: dict[str, UUID] = {}
    if destination.project_id:
        filters["project_id"] = destination.project_id
    elif destination.program_id:
        filters["program_id"] = destination.program_id
    data = TaskService(db).list_due_tasks(account_id=destination.account_id, current_user=viewer, mode="OVERDUE", days=0, **filters)
    tasks = list(data.get("tasks") or [])  # type: ignore[union-attr]
    if destination.portfolio_id:
        portfolio_projects = {p.id for p in db.query(Project).filter_by(portfolio_id=destination.portfolio_id)}
        tasks = [task for task in tasks if _get(task, "project_id") in portfolio_projects]
    if not tasks:
        return None
    items = []
    for task in tasks[:OVERDUE_LIMIT]:
        due = _get(task, "finish_date")
        project = _get(_get(task, "project") or {}, "name")
        detail = ", ".join(part for part in (f"due {due}" if due else "", str(project or "")) if part)
        items.append((f"{_get(task, 'name')} ({detail})" if detail else str(_get(task, "name")), f"/projects/{_get(task, 'project_id')}/sheet?task={_get(task, 'id')}"))
    more = len(tasks) - len(items)
    return ChatMessage(
        title=f"{len(tasks)} overdue task{'s' if len(tasks) != 1 else ''} in {scope_name(db, destination)}",
        items=items,
        lines=[f"…and {more} more."] if more > 0 else [],
        link="/ResourceTaskPulse",
        link_label="See all due tasks",
    )


def _get(row: object, key: str) -> object:
    return row.get(key) if isinstance(row, dict) else getattr(row, key, None)
