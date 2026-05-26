from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AttachmentPresignedUploadCreate(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_type: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = Field(default=None, gt=0)


class AttachmentRead(BaseModel):
    id: UUID
    account_id: UUID
    entity_type: str
    entity_id: UUID
    file_name: str
    s3_key: str
    content_type: str | None
    size_bytes: int | None
    uploaded_by: UUID | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AttachmentPresignedUploadRead(BaseModel):
    attachment_id: UUID
    s3_key: str
    upload_url: str
    method: str
    headers: dict[str, str]


class AttachmentPresignedDownloadRead(BaseModel):
    download_url: str