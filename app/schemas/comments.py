from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CommentCreate(BaseModel):
    body: str = Field(min_length=1)
    parent_comment_id: UUID | None = None


class CommentUpdate(BaseModel):
    body: str = Field(min_length=1)


class CommentAttachmentSummary(BaseModel):
    id: UUID
    file_name: str
    content_type: str | None

    model_config = ConfigDict(from_attributes=True)


class CommentRead(BaseModel):
    id: UUID
    account_id: UUID
    entity_type: str
    entity_id: UUID
    body: str
    parent_comment_id: UUID | None = None
    created_by: UUID | None
    created_by_name: str | None = None
    attachments: list[CommentAttachmentSummary] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CommentMentionRead(BaseModel):
    id: UUID
    account_id: UUID
    comment_id: UUID
    mentioned_user_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)