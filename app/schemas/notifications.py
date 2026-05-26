from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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