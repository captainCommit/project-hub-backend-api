from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NotificationType(str, Enum):
    TASK_ASSIGNED = "TASK_ASSIGNED"
    COMMENT_ADDED = "COMMENT_ADDED"
    MENTION = "MENTION"
    STATUS_CHANGED = "STATUS_CHANGED"
    RISK_CREATED = "RISK_CREATED"
    DECISION_APPROVED = "DECISION_APPROVED"
    DUE_SOON = "DUE_SOON"
    # Project Hub support answered the person's support ticket.
    SUPPORT_REPLY = "SUPPORT_REPLY"


class EmailStatus(str, Enum):
    """Where a notification's email is. NULL: no email (turned off, or sent before emails existed)."""

    PENDING = "PENDING"  # waiting for the delivery worker
    SENDING = "SENDING"  # claimed by a worker run
    DIGEST = "DIGEST"  # waiting for the person's daily digest
    SENT = "SENT"
    SKIPPED = "SKIPPED"  # dropped at send time: turned off, no longer visible, or address suppressed
    FAILED = "FAILED"  # SES refused it too many times


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "notification_type in ("
            "'TASK_ASSIGNED', 'COMMENT_ADDED', 'MENTION', "
            "'STATUS_CHANGED', 'RISK_CREATED', 'DECISION_APPROVED', 'DUE_SOON', 'SUPPORT_REPLY'"
            ")",
            name="ck_notifications_notification_type",
        ),
        CheckConstraint(
            "email_status is null or email_status in ('PENDING', 'SENDING', 'DIGEST', 'SENT', 'SKIPPED', 'FAILED')",
            name="ck_notifications_email_status",
        ),
        Index("ix_notifications_email_status_created_at", "email_status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    email_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    email_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Removed from the person's bell. The row stays so a pending email or digest still goes out.
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
