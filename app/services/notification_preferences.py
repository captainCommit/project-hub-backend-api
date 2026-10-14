from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.email_suppression import EmailSuppression
from app.models.notification import NotificationType
from app.models.notification_preference import (
    DEFAULT_DIGEST_HOUR,
    DEFAULT_TIMEZONE,
    EMAIL_CATEGORY_DEFAULTS,
    EmailFrequency,
    NotificationPreference,
)
from app.models.user import User

# Not a notification category: an on/off switch for the My work part of the daily email (IN-2).
MY_WORK_SUMMARY = "my_work_summary"
# What an unsubscribe link can turn off, besides "all".
UNSUBSCRIBE_CATEGORIES = (*EMAIL_CATEGORY_DEFAULTS, MY_WORK_SUMMARY)

# Which preference decides whether a notification is emailed.
CATEGORY_BY_TYPE: dict[str, str] = {
    NotificationType.MENTION.value: "mentions",
    NotificationType.TASK_ASSIGNED.value: "assignments",
    NotificationType.RISK_CREATED.value: "assignments",  # only sent to the person the risk is assigned to
    NotificationType.DUE_SOON.value: "due_soon",
    NotificationType.STATUS_CHANGED.value: "status_changes",
    NotificationType.DECISION_APPROVED.value: "status_changes",
    NotificationType.COMMENT_ADDED.value: "comments",
}

# Shown in emails: "You get these emails for ...".
CATEGORY_LABELS = {
    "mentions": "mentions",
    "assignments": "assignments",
    "due_soon": "due-soon reminders",
    "status_changes": "status changes",
    "comments": "comments",
    MY_WORK_SUMMARY: "your daily My work summary",
}


def category_for(notification_type: str) -> str | None:
    return CATEGORY_BY_TYPE.get(notification_type)


def default_preference(user_id: UUID) -> NotificationPreference:
    return NotificationPreference(
        user_id=user_id,
        **{category: frequency.value for category, frequency in EMAIL_CATEGORY_DEFAULTS.items()},
        timezone=DEFAULT_TIMEZONE,
        digest_hour=DEFAULT_DIGEST_HOUR,
        timezone_confirmed=False,
        my_work_summary=True,
    )


def zone_for(preference: NotificationPreference) -> ZoneInfo:
    try:
        return ZoneInfo(preference.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


class NotificationPreferenceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, user_id: UUID) -> NotificationPreference | None:
        return self.db.get(NotificationPreference, user_id)

    def get_or_default(self, user_id: UUID) -> NotificationPreference:
        """The saved settings, or unsaved defaults (not added to the session)."""
        return self.get(user_id) or default_preference(user_id)

    def get_or_create(self, user_id: UUID) -> NotificationPreference:
        preference = self.get(user_id)
        if preference is None:
            preference = default_preference(user_id)
            self.db.add(preference)
            self.db.flush()
        return preference

    def frequency_for(self, user_id: UUID, notification_type: str) -> EmailFrequency:
        # An answer to someone's own support ticket is always emailed straight away.
        if notification_type == NotificationType.SUPPORT_REPLY.value:
            return EmailFrequency.IMMEDIATE
        category = category_for(notification_type)
        if category is None:
            return EmailFrequency.OFF
        return EmailFrequency(getattr(self.get_or_default(user_id), category))

    def is_suppressed(self, email: str) -> bool:
        return self.db.get(EmailSuppression, email.strip().lower()) is not None

    def read(self, user: User) -> dict[str, object]:
        from app.services.integrations.mail import can_send_to

        preference = self.get_or_default(user.id)
        return {
            **{category: getattr(preference, category) for category in EMAIL_CATEGORY_DEFAULTS},
            "timezone": preference.timezone,
            "digest_hour": preference.digest_hour,
            "timezone_confirmed": preference.timezone_confirmed,
            MY_WORK_SUMMARY: preference.my_work_summary,
            "email": user.email,
            "email_available": can_send_to(self.db, get_settings(), user.id),
            "email_suppressed": self.is_suppressed(user.email),
        }

    def update(self, user: User, changes: dict[str, object]) -> dict[str, object]:
        if "timezone" in changes:
            try:
                ZoneInfo(str(changes["timezone"]))
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown timezone.") from exc
        preference = self.get_or_create(user.id)
        for field, value in changes.items():
            setattr(preference, field, value.value if isinstance(value, EmailFrequency) else value)
        if "timezone" in changes:
            preference.timezone_confirmed = True
        self.db.add(preference)
        self.db.commit()
        return self.read(user)

    def unsubscribe(self, user_id: UUID, category: str) -> None:
        """Turn off one category, the My work summary, or everything when category is "all"."""
        preference = self.get_or_create(user_id)
        if category in (MY_WORK_SUMMARY, "all"):
            preference.my_work_summary = False
        categories = list(EMAIL_CATEGORY_DEFAULTS) if category == "all" else [category]
        for name in categories:
            if name in EMAIL_CATEGORY_DEFAULTS:
                setattr(preference, name, EmailFrequency.OFF.value)
        self.db.add(preference)
        self.db.commit()
