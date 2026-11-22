from datetime import datetime
from enum import Enum
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.account_member import AccountMemberRole
from app.schemas.access import InviteAccess


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountUserInviteRole(str, Enum):
    OWNER = "OWNER"
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
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    # Job title and department (Add User): the person's position, used for resource planning.
    title: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    # The account role to give them (Account > Users & Roles). Older callers send a seat role instead.
    role_id: UUID | None = None
    role: AccountUserInviteRole | None = None
    update_existing: bool = False
    # Optional: the portfolio, program or project they start on, at the invited role. Ignored for owners.
    access: InviteAccess | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, email: str) -> str:
        return normalize_and_validate_email(email)

    @field_validator("full_name", "first_name", "last_name", "title", "department")
    @classmethod
    def normalize_full_name(cls, full_name: str | None) -> str | None:
        if full_name is None:
            return None
        normalized = full_name.strip()
        return normalized or None

    @model_validator(mode="after")
    def role_given(self) -> "AccountUserInviteCreate":
        if self.role is None and self.role_id is None:
            raise ValueError("Give a role_id (or a role).")
        if self.full_name is None and (self.first_name or self.last_name):
            self.full_name = " ".join(part for part in (self.first_name, self.last_name) if part)
        return self


class AccountUserRoleUpdate(BaseModel):
    role_id: UUID | None = None
    role: AccountUserInviteRole | None = None

    @model_validator(mode="after")
    def role_given(self) -> "AccountUserRoleUpdate":
        if self.role is None and self.role_id is None:
            raise ValueError("Give a role_id (or a role).")
        return self


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
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    department: str | None = None
    # Their seat: OWNER, MEMBER (full) or VIEWER.
    role: AccountMemberRole | None = None
    # Their account role (Account > Users & Roles).
    role_id: UUID | None = None
    role_name: str | None = None
    # False until the person signs in for the first time (an invite not yet accepted).
    has_signed_in: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)