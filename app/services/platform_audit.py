from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams
from app.core.request_context import get_request_id
from app.models.account import Account
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.user import User


class PlatformAuditAction:
    ACCOUNT_CREATED = "account.created"
    ACCOUNT_USERS_VIEWED = "account.users_viewed"
    ACCOUNT_USER_INVITED = "account.user_invited"
    ACCOUNT_USER_ROLE_CHANGED = "account.user_role_changed"
    ACCOUNT_USER_REMOVED = "account.user_removed"
    ACCOUNT_USERS_BULK_INVITED = "account.users_bulk_invited"
    ACCOUNT_BILLING_ENABLED = "account.billing_enabled"
    ACCOUNT_BILLING_DISABLED = "account.billing_disabled"
    ACCOUNT_BILLING_SYNCED = "account.billing_synced"
    SUPPORT_TICKET_REPLIED = "support.ticket_replied"
    SUPPORT_TICKET_UPDATED = "support.ticket_updated"


def record_platform_action(
    db: Session,
    *,
    actor: User,
    action: str,
    account: Account | None = None,
    target: str | None = None,
    details: dict[str, object] | None = None,
) -> PlatformAuditEvent:
    """Add an audit entry to the current transaction; the caller's commit saves it with the change it describes."""
    event = PlatformAuditEvent(
        actor_user_id=actor.id,
        actor_email=actor.email,
        action=action,
        account_id=account.id if account else None,
        account_name=account.name if account else None,
        target=target,
        details=details,
        request_id=get_request_id(),
    )
    db.add(event)
    return event


def list_platform_actions(
    db: Session,
    *,
    pagination: PaginationParams,
    account_id: UUID | None = None,
) -> tuple[list[PlatformAuditEvent], int]:
    statement = select(PlatformAuditEvent)
    if account_id is not None:
        statement = statement.where(PlatformAuditEvent.account_id == account_id)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = db.scalars(
        statement.order_by(PlatformAuditEvent.created_at.desc(), PlatformAuditEvent.id)
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    return list(items), total
