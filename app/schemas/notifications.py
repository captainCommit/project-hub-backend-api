from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationRead(BaseModel):
    id: UUID
    account_id: UUID
    user_id: UUID
    entity_type: str
    entity_id: UUID
    notification_type: str
    title: str
    message: str | None
    is_read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    results: list[NotificationRead]
    page: int
    page_size: int
    total: int

EmailFrequencyValue = Literal["IMMEDIATE", "DIGEST", "OFF"]


class NotificationPreferencesRead(BaseModel):
    mentions: EmailFrequencyValue
    assignments: EmailFrequencyValue
    due_soon: EmailFrequencyValue
    status_changes: EmailFrequencyValue
    comments: EmailFrequencyValue
    timezone: str
    digest_hour: int
    # False while the timezone is still the default nobody chose; the app then saves the browser's zone.
    timezone_confirmed: bool
    # The daily email's Home summary (overdue, due today, blocked, new mentions).
    my_work_summary: bool
    email: str
    # False when the server has no email sender set up: settings save, but nothing is sent yet.
    email_available: bool
    # SES reported a hard bounce or spam complaint for this address, so nothing is sent to it.
    email_suppressed: bool


class NotificationPreferencesUpdate(BaseModel):
    mentions: EmailFrequencyValue | None = None
    assignments: EmailFrequencyValue | None = None
    due_soon: EmailFrequencyValue | None = None
    status_changes: EmailFrequencyValue | None = None
    comments: EmailFrequencyValue | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    digest_hour: int | None = Field(default=None, ge=0, le=23)
    my_work_summary: bool | None = None
