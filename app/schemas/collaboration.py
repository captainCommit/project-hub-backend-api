from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def upper_key(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


ItemType = Literal["PORTFOLIO", "PROGRAM", "PROJECT"]
AccessLevel = Literal["VIEW", "EDIT"]


class SharedAccessCreate(BaseModel):
    item_type: ItemType
    item_id: UUID
    shared_with_email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+$")
    access_level: AccessLevel = "VIEW"

    _normalize_keys = field_validator("item_type", "access_level", mode="before")(upper_key)

    @field_validator("shared_with_email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class SharedAccessUpdate(BaseModel):
    access_level: AccessLevel

    _normalize_keys = field_validator("access_level", mode="before")(upper_key)


class SharedAccessRead(BaseModel):
    id: UUID
    account_id: UUID
    item_type: str
    item_id: UUID
    shared_with_email: str
    access_level: str
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TemplateCreate(BaseModel):
    template_type: ItemType = "PROJECT"
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    color: str | None = Field(default=None, max_length=50)
    content: dict[str, Any] = Field(default_factory=dict)

    _normalize_keys = field_validator("template_type", mode="before")(upper_key)


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    color: str | None = Field(default=None, max_length=50)
    content: dict[str, Any] | None = None


class TemplateRead(BaseModel):
    id: UUID
    account_id: UUID
    template_type: str
    name: str
    description: str | None
    color: str | None
    content: dict[str, Any]
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


TicketCategory = Literal["GENERAL", "TECHNICAL", "BILLING", "BUG", "FEATURE_REQUEST", "ACCOUNT"]
TicketPriority = Literal["LOW", "MEDIUM", "HIGH"]
TicketStatus = Literal["OPEN", "AWAITING_RESPONSE", "IN_PROGRESS", "RESOLVED", "CLOSED"]


class TicketAttachmentRef(BaseModel):
    attachment_id: UUID
    name: str = Field(min_length=1, max_length=255)


class SupportTicketCreate(BaseModel):
    category: TicketCategory = "GENERAL"
    subject: str | None = Field(default=None, max_length=255)
    description: str = Field(min_length=1)
    priority: TicketPriority = "MEDIUM"
    requester_name: str | None = Field(default=None, max_length=255)
    requester_email: str | None = Field(default=None, max_length=255)

    _normalize_keys = field_validator("category", "priority", mode="before")(upper_key)


class SupportTicketUpdate(BaseModel):
    subject: str | None = Field(default=None, max_length=255)
    priority: TicketPriority | None = None
    status: TicketStatus | None = None

    _normalize_keys = field_validator("priority", "status", mode="before")(upper_key)


class SupportTicketMessageCreate(BaseModel):
    content: str = Field(min_length=1)
    attachments: list[TicketAttachmentRef] = Field(default_factory=list)


class SupportTicketRead(BaseModel):
    id: UUID
    account_id: UUID
    ticket_number: str
    category: str
    subject: str | None
    description: str
    priority: str
    status: str
    requester_user_id: UUID | None
    requester_name: str
    requester_email: str
    messages: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
