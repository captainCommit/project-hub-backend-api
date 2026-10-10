from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        UniqueConstraint("account_id", "ticket_number", name="uq_support_tickets_account_id_ticket_number"),
        CheckConstraint(
            "category in ('GENERAL', 'TECHNICAL', 'BILLING', 'BUG', 'FEATURE_REQUEST', 'ACCOUNT')",
            name="ck_support_tickets_category",
        ),
        CheckConstraint("priority in ('LOW', 'MEDIUM', 'HIGH')", name="ck_support_tickets_priority"),
        CheckConstraint(
            "status in ('OPEN', 'AWAITING_RESPONSE', 'IN_PROGRESS', 'RESOLVED', 'CLOSED')",
            name="ck_support_tickets_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticket_number: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False, default="GENERAL", server_default="GENERAL")
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="MEDIUM", server_default="MEDIUM")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN", server_default="OPEN")
    requester_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    requester_name: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_email: Mapped[str] = mapped_column(String(255), nullable=False)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
