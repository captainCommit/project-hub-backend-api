from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.shared_access import SharedAccess
from app.models.support_ticket import SupportTicket
from app.models.template import Template
from app.models.user import User
from app.schemas.collaboration import (
    SharedAccessCreate,
    SharedAccessUpdate,
    SupportTicketCreate,
    SupportTicketMessageCreate,
    SupportTicketUpdate,
    TemplateCreate,
    TemplateUpdate,
)
from app.services.membership import ADMIN_ROLES, WRITE_ROLES, require_account_member, require_account_role


SHAREABLE_MODELS = {"PORTFOLIO": Portfolio, "PROGRAM": Program, "PROJECT": Project}


def apply_changes(item: object, changes: dict[str, object]) -> None:
    for field, value in changes.items():
        setattr(item, field, value)


class SharedAccessService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def resolve_item_account(self, *, item_type: str, item_id: UUID) -> UUID:
        model_cls = SHAREABLE_MODELS.get(item_type.strip().upper())
        if model_cls is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported shared item type.")
        item = self.db.get(model_cls, item_id)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared item not found.")
        return item.account_id

    def list_shares(self, *, item_type: str, item_id: UUID, current_user: User) -> list[SharedAccess]:
        account_id = self.resolve_item_account(item_type=item_type, item_id=item_id)
        require_account_member(self.db, account_id=account_id, user_id=current_user.id)
        statement = (
            select(SharedAccess)
            .where(SharedAccess.item_type == item_type.strip().upper(), SharedAccess.item_id == item_id)
            .order_by(SharedAccess.created_at, SharedAccess.id)
        )
        return list(self.db.scalars(statement).all())

    def create_share(self, *, share_in: SharedAccessCreate, current_user: User) -> SharedAccess:
        account_id = self.resolve_item_account(item_type=share_in.item_type, item_id=share_in.item_id)
        require_account_role(self.db, account_id=account_id, user_id=current_user.id, allowed_roles=WRITE_ROLES)
        share = SharedAccess(account_id=account_id, created_by=current_user.id, **share_in.model_dump())
        self.db.add(share)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This item is already shared with that email.",
            ) from exc
        self.db.refresh(share)
        return share

    def update_share(self, *, share_id: UUID, share_in: SharedAccessUpdate, current_user: User) -> SharedAccess:
        share = self.get_share_or_404(share_id)
        require_account_role(self.db, account_id=share.account_id, user_id=current_user.id, allowed_roles=WRITE_ROLES)
        apply_changes(share, share_in.model_dump(exclude_unset=True))
        self.db.commit()
        self.db.refresh(share)
        return share

    def delete_share(self, *, share_id: UUID, current_user: User) -> None:
        share = self.get_share_or_404(share_id)
        require_account_role(self.db, account_id=share.account_id, user_id=current_user.id, allowed_roles=WRITE_ROLES)
        self.db.delete(share)
        self.db.commit()

    def get_share_or_404(self, share_id: UUID) -> SharedAccess:
        share = self.db.get(SharedAccess, share_id)
        if share is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared access not found.")
        return share


class TemplateService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_templates(self, *, account_id: UUID, template_type: str | None, current_user: User) -> list[Template]:
        require_account_member(self.db, account_id=account_id, user_id=current_user.id)
        statement = select(Template).where(Template.account_id == account_id)
        if template_type:
            statement = statement.where(Template.template_type == template_type.strip().upper())
        return list(self.db.scalars(statement.order_by(Template.name, Template.id)).all())

    def create_template(self, *, account_id: UUID, template_in: TemplateCreate, current_user: User) -> Template:
        require_account_role(self.db, account_id=account_id, user_id=current_user.id, allowed_roles=WRITE_ROLES)
        template = Template(account_id=account_id, created_by=current_user.id, **template_in.model_dump())
        self.db.add(template)
        self.db.commit()
        self.db.refresh(template)
        return template

    def get_template(self, *, template_id: UUID, current_user: User) -> Template:
        template = self.get_template_or_404(template_id)
        require_account_member(self.db, account_id=template.account_id, user_id=current_user.id)
        return template

    def update_template(self, *, template_id: UUID, template_in: TemplateUpdate, current_user: User) -> Template:
        template = self.get_template_or_404(template_id)
        require_account_role(
            self.db, account_id=template.account_id, user_id=current_user.id, allowed_roles=WRITE_ROLES
        )
        changes = template_in.model_dump(exclude_unset=True)
        if changes.get("content") is None:
            changes.pop("content", None)
        apply_changes(template, changes)
        self.db.commit()
        self.db.refresh(template)
        return template

    def delete_template(self, *, template_id: UUID, current_user: User) -> None:
        template = self.get_template_or_404(template_id)
        require_account_role(
            self.db, account_id=template.account_id, user_id=current_user.id, allowed_roles=WRITE_ROLES
        )
        self.db.delete(template)
        self.db.commit()

    def get_template_or_404(self, template_id: UUID) -> Template:
        template = self.db.get(Template, template_id)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found.")
        return template


class SupportTicketService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_tickets(self, *, account_id: UUID, current_user: User) -> list[SupportTicket]:
        membership = require_account_member(self.db, account_id=account_id, user_id=current_user.id)
        statement = select(SupportTicket).where(SupportTicket.account_id == account_id)
        if membership.role not in ADMIN_ROLES:
            statement = statement.where(SupportTicket.requester_user_id == current_user.id)
        return list(self.db.scalars(statement.order_by(SupportTicket.updated_at.desc(), SupportTicket.id)).all())

    def create_ticket(self, *, account_id: UUID, ticket_in: SupportTicketCreate, current_user: User) -> SupportTicket:
        require_account_member(self.db, account_id=account_id, user_id=current_user.id)
        requester_name = ticket_in.requester_name or current_user.full_name or current_user.email
        values = ticket_in.model_dump(exclude={"requester_name", "requester_email"})
        ticket = SupportTicket(
            account_id=account_id,
            ticket_number=self.next_ticket_number(account_id),
            requester_user_id=current_user.id,
            requester_name=requester_name,
            requester_email=ticket_in.requester_email or current_user.email,
            status="OPEN",
            messages=[self.message(role="user", author=requester_name, content=ticket_in.description)],
            **values,
        )
        self.db.add(ticket)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ticket number conflict, retry.") from exc
        self.db.refresh(ticket)
        return ticket

    def get_ticket(self, *, ticket_id: UUID, current_user: User) -> SupportTicket:
        ticket = self.get_ticket_or_404(ticket_id)
        self.require_ticket_access(ticket, current_user)
        return ticket

    def update_ticket(self, *, ticket_id: UUID, ticket_in: SupportTicketUpdate, current_user: User) -> SupportTicket:
        ticket = self.get_ticket_or_404(ticket_id)
        self.require_ticket_access(ticket, current_user)
        apply_changes(ticket, {k: v for k, v in ticket_in.model_dump(exclude_unset=True).items() if v is not None})
        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    def add_message(
        self,
        *,
        ticket_id: UUID,
        message_in: SupportTicketMessageCreate,
        current_user: User,
    ) -> SupportTicket:
        ticket = self.get_ticket_or_404(ticket_id)
        membership = self.require_ticket_access(ticket, current_user)
        is_requester = ticket.requester_user_id == current_user.id
        role = "user" if is_requester or membership.role not in ADMIN_ROLES else "support"
        author = ticket.requester_name if role == "user" else (current_user.full_name or current_user.email)
        message = self.message(
            role=role,
            author=author,
            content=message_in.content,
            attachments=[attachment.model_dump(mode="json") for attachment in message_in.attachments],
        )
        # Reassign rather than mutate so SQLAlchemy detects the JSON change.
        ticket.messages = [*ticket.messages, message]
        ticket.status = "OPEN" if role == "user" else "AWAITING_RESPONSE"
        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    def require_ticket_access(self, ticket: SupportTicket, current_user: User):
        membership = require_account_member(self.db, account_id=ticket.account_id, user_id=current_user.id)
        if ticket.requester_user_id != current_user.id and membership.role not in ADMIN_ROLES:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support ticket not found.")
        return membership

    def get_ticket_or_404(self, ticket_id: UUID) -> SupportTicket:
        ticket = self.db.get(SupportTicket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support ticket not found.")
        return ticket

    def next_ticket_number(self, account_id: UUID) -> str:
        count = self.db.scalar(select(func.count()).where(SupportTicket.account_id == account_id)) or 0
        return f"TKT-{count + 1:04d}"

    @staticmethod
    def message(*, role: str, author: str, content: str, attachments: list[dict[str, object]] | None = None) -> dict[str, object]:
        return {
            "role": role,
            "author": author,
            "content": content,
            "attachments": attachments or [],
            "date": datetime.now(timezone.utc).isoformat(),
        }
