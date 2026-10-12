from datetime import date, datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmailFrequency(str, Enum):
    IMMEDIATE = "IMMEDIATE"
    DIGEST = "DIGEST"
    OFF = "OFF"


# Email categories a person can tune, with the default for anyone who hasn't changed them.
# Comments go to everyone who can see the item, so they stay off unless someone asks for them.
EMAIL_CATEGORY_DEFAULTS: dict[str, EmailFrequency] = {
    "mentions": EmailFrequency.IMMEDIATE,
    "assignments": EmailFrequency.IMMEDIATE,
    "due_soon": EmailFrequency.IMMEDIATE,
    "status_changes": EmailFrequency.DIGEST,
    "comments": EmailFrequency.OFF,
}

DEFAULT_TIMEZONE = "America/Toronto"
DEFAULT_DIGEST_HOUR = 8

_FREQUENCY_CHECK = "in ('IMMEDIATE', 'DIGEST', 'OFF')"


class NotificationPreference(Base):
    """One person's email settings. People without a row get the defaults above."""

    __tablename__ = "notification_preferences"
    __table_args__ = tuple(
        CheckConstraint(f"{category} {_FREQUENCY_CHECK}", name=f"ck_notification_preferences_{category}")
        for category in EMAIL_CATEGORY_DEFAULTS
    ) + (
        CheckConstraint("digest_hour >= 0 and digest_hour <= 23", name="ck_notification_preferences_digest_hour"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mentions: Mapped[str] = mapped_column(String(10), nullable=False, default=EmailFrequency.IMMEDIATE.value)
    assignments: Mapped[str] = mapped_column(String(10), nullable=False, default=EmailFrequency.IMMEDIATE.value)
    due_soon: Mapped[str] = mapped_column(String(10), nullable=False, default=EmailFrequency.IMMEDIATE.value)
    status_changes: Mapped[str] = mapped_column(String(10), nullable=False, default=EmailFrequency.DIGEST.value)
    comments: Mapped[str] = mapped_column(String(10), nullable=False, default=EmailFrequency.OFF.value)
    # IANA name; the digest and due-soon reminders go out at digest_hour in this zone.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default=DEFAULT_TIMEZONE)
    digest_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_DIGEST_HOUR)
    # Local dates of the last morning run, so each happens once a day.
    last_digest_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_due_soon_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
