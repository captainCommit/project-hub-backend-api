from datetime import datetime
from enum import Enum
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.account_member import AccountMemberRole


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountUserInviteRole(str, Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"


class AccountUserInviteStatus(str, Enum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    FAILED = "FAILED"


def normalize_and_validate_email(email: str) -> str:
    normalized = email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Invalid email format.")
    return normalized


class AccountUserInviteCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    role: AccountUserInviteRole
    update_existing: bool = False

    @field_validator("email")
    @classmethod
    def validate_email(cls, email: str) -> str:
        return normalize_and_validate_email(email)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, full_name: str | None) -> str | None:
        if full_name is None:
            return None
        normalized = full_name.strip()
        return normalized or None


class AccountUserBulkInviteItem(BaseModel):
    email: Any = None
    full_name: Any = None
    role: Any = None
    update_existing: Any = None

    model_config = ConfigDict(extra="allow")


class AccountUserBulkInviteCreate(BaseModel):
    users: list[AccountUserBulkInviteItem] = Field(min_length=1)
    update_existing: bool = False


class AccountUserInviteRead(BaseModel):
    email: str
    status: AccountUserInviteStatus
    user_id: UUID
    account_id: UUID
    membership_id: UUID
    full_name: str | None
    role: AccountMemberRole
    error: str | None = None


class AccountUserBulkInviteResult(BaseModel):
    email: str | None
    status: AccountUserInviteStatus
    user_id: UUID | None = None
    role: AccountMemberRole | None = None
    error: str | None = None


class AccountUserBulkInviteRead(BaseModel):
    created: int
    updated: int
    already_exists: int
    failed: int
    results: list[AccountUserBulkInviteResult]


class AccountUserRead(BaseModel):
    id: UUID
    email: str
    full_name: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)