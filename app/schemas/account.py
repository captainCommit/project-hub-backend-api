from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.account_member import AccountMemberRole
from app.schemas.account_user import normalize_and_validate_email


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    # When set, this person is invited as the account's ADMIN instead of the creator becoming OWNER.
    admin_email: str | None = Field(default=None, min_length=3, max_length=255)
    admin_full_name: str | None = Field(default=None, max_length=255)

    @field_validator("admin_email")
    @classmethod
    def validate_admin_email(cls, email: str | None) -> str | None:
        if email is None or not email.strip():
            return None
        return normalize_and_validate_email(email)

    @field_validator("admin_full_name")
    @classmethod
    def normalize_admin_full_name(cls, full_name: str | None) -> str | None:
        if full_name is None:
            return None
        return full_name.strip() or None


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=255)


class AccountRead(BaseModel):
    id: UUID
    name: str
    slug: str
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    # The current user's role in this account; set by the account list.
    role: AccountMemberRole | None = None

    model_config = ConfigDict(from_attributes=True)


class AccountAdminContact(BaseModel):
    email: str
    full_name: str | None
    role: AccountMemberRole


class PlatformAccountRead(BaseModel):
    """An account as platform admins see it: every account, with its owners and admins."""

    id: UUID
    name: str
    slug: str
    created_at: datetime
    member_count: int
    admins: list[AccountAdminContact]
