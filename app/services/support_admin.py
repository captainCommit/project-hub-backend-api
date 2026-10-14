"""Project Hub support: platform admins see and answer every account's support tickets.

Customers raise tickets from Help Center > Contact support (SupportTicketService); each belongs to one account and
only its requester and the account's owners can open it there. Here platform admins work the queue across all
accounts: list, read, reply (as "Project Hub Support") and change status or priority. Replies notify the requester
in the app and by email, and every change is in the platform audit log.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.notification import NotificationType
from app.models.support_ticket import SupportTicket
from app.models.user import User
from app.schemas.collaboration import SupportTicketMessageCreate
from app.services.collaboration import SupportTicketService
from app.services.notifications import NotificationService
from app.services.platform_audit import PlatformAuditAction, record_platform_action

SUPPORT_AUTHOR = "Project Hub Support"
CLOSED_STATUSES = {"RESOLVED", "CLOSED"}
LIST_LIMIT = 200


def needs_reply(ticket: SupportTicket) -> bool:
    """Open, and the customer spoke last (or nobody has answered yet)."""
    if ticket.status in CLOSED_STATUSES:
        return False
    messages = ticket.messages or []
    return not messages or messages[-1].get("role") != "support"


class SupportAdminService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list(self, *, status_filter: str | None = None, search: str | None = None, account_id: UUID | None = None) -> list[dict[str, Any]]:
        """Newest activity first. status_filter: a ticket status, "ACTIVE" (not resolved or closed), or "ALL"."""
        query = select(SupportTicket, Account.name).join(Account, Account.id == SupportTicket.account_id)
        if status_filter == "ACTIVE":
            query = query.where(SupportTicket.status.not_in(CLOSED_STATUSES))
        elif status_filter and status_filter != "ALL":
            query = query.where(SupportTicket.status == status_filter)
        if account_id:
            query = query.where(SupportTicket.account_id == account_id)
        if search and search.strip():
            like = f"%{search.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(SupportTicket.subject).like(like),
                    func.lower(SupportTicket.description).like(like),
                    func.lower(SupportTicket.requester_email).like(like),
                    func.lower(SupportTicket.ticket_number).like(like),
                    func.lower(Account.name).like(like),
                )
            )
        rows = self.db.execute(query.order_by(SupportTicket.updated_at.desc()).limit(LIST_LIMIT)).all()
        return [self.present(ticket, account_name, with_messages=False) for ticket, account_name in rows]

    def counts(self) -> dict[str, int]:
        tickets = self.db.scalars(select(SupportTicket).where(SupportTicket.status.not_in(CLOSED_STATUSES))).all()
        return {"active": len(tickets), "needs_reply": sum(1 for t in tickets if needs_reply(t))}

    def get(self, ticket_id: UUID) -> SupportTicket:
        ticket = self.db.get(SupportTicket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support ticket not found.")
        return ticket

    def detail(self, ticket_id: UUID) -> dict[str, Any]:
        ticket = self.get(ticket_id)
        account = self.db.get(Account, ticket.account_id)
        return self.present(ticket, account.name if account else "")

    def reply(self, *, ticket_id: UUID, message_in: SupportTicketMessageCreate, actor: User) -> dict[str, Any]:
        ticket = self.get(ticket_id)
        account = self.db.get(Account, ticket.account_id)
        message = SupportTicketService.message(
            role="support",
            author=SUPPORT_AUTHOR,
            content=message_in.content,
            attachments=[attachment.model_dump(mode="json") for attachment in message_in.attachments],
        )
        # Who answered, for the support team; customers only see SUPPORT_AUTHOR.
        message["agent"] = actor.full_name or actor.email
        # Reassign rather than mutate so SQLAlchemy detects the JSON change.
        ticket.messages = [*(ticket.messages or []), message]
        if ticket.status not in CLOSED_STATUSES:
            ticket.status = "AWAITING_RESPONSE"
        ticket.updated_at = datetime.now(UTC)
        record_platform_action(self.db, actor=actor, action=PlatformAuditAction.SUPPORT_TICKET_REPLIED, account=account, target=ticket.ticket_number)
        if ticket.requester_user_id:
            NotificationService(self.db).create_notification(
                account_id=ticket.account_id,
                user_id=ticket.requester_user_id,
                entity_type="SUPPORT_TICKET",
                entity_id=ticket.id,
                notification_type=NotificationType.SUPPORT_REPLY,
                title=f"Project Hub Support replied to {ticket.ticket_number}",
                message=message_in.content[:300],
                actor_user_id=actor.id,
            )
        self.db.commit()
        return self.present(ticket, account.name if account else "")

    def update(self, *, ticket_id: UUID, changes: dict[str, Any], actor: User) -> dict[str, Any]:
        ticket = self.get(ticket_id)
        account = self.db.get(Account, ticket.account_id)
        applied = {field: value for field, value in changes.items() if value is not None and getattr(ticket, field) != value}
        for field, value in applied.items():
            setattr(ticket, field, value)
        if applied:
            ticket.updated_at = datetime.now(UTC)
            record_platform_action(
                self.db, actor=actor, action=PlatformAuditAction.SUPPORT_TICKET_UPDATED, account=account, target=ticket.ticket_number, details=applied
            )
        self.db.commit()
        return self.present(ticket, account.name if account else "")

    def present(self, ticket: SupportTicket, account_name: str, *, with_messages: bool = True) -> dict[str, Any]:
        messages = ticket.messages or []
        return {
            "id": ticket.id,
            "account_id": ticket.account_id,
            "account_name": account_name,
            "ticket_number": ticket.ticket_number,
            "category": ticket.category,
            "subject": ticket.subject,
            "description": ticket.description,
            "priority": ticket.priority,
            "status": ticket.status,
            "requester_name": ticket.requester_name,
            "requester_email": ticket.requester_email,
            "message_count": len(messages),
            "needs_reply": needs_reply(ticket),
            "messages": messages if with_messages else [],
            "created_at": ticket.created_at,
            "updated_at": ticket.updated_at,
        }
