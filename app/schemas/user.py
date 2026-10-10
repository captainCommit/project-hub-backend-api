from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserRead(BaseModel):
    id: UUID
    cognito_sub: str | None
    email: str
    full_name: str | None
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    department: str | None = None
    company: str | None = None
    phone: str | None = None
    photo_url: str | None = None
    is_active: bool
    is_platform_admin: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    remove_photo: bool = False


class UserPhotoUploadRead(BaseModel):
    upload_url: str
    method: str
    headers: dict[str, str]
    s3_key: str
