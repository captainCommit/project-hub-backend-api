from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AiProviderValue = Literal["OPENAI", "ANTHROPIC", "GEMINI", "AZURE_OPENAI", "CUSTOM"]


class AiStatusRead(BaseModel):
    """What every member needs to know before showing the chat box."""

    available: bool
    # Where the model comes from: the account's own key, or the platform.
    source: Literal["BYOK", "PLATFORM"] | None
    # Why it isn't available: switched off by an owner, or no model set up.
    reason: Literal["OFF", "NOT_SET_UP", "LIMIT_REACHED"] | None
    provider_label: str | None
    model: str | None
    can_manage: bool


class AiKeyRead(BaseModel):
    provider: AiProviderValue
    provider_label: str
    model: str
    base_url: str | None
    azure_api_version: str | None
    key_last4: str | None
    key_updated_at: datetime | None
    key_updated_by_name: str | None


class AiSettingsRead(BaseModel):
    assistant_enabled: bool
    retention_days: int
    key: AiKeyRead | None
    # False when this server has nowhere safe to keep keys (no KMS key configured).
    key_storage_available: bool


class AiSettingsUpdate(BaseModel):
    assistant_enabled: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class AiKeyUpdate(BaseModel):
    provider: AiProviderValue
    model: str = Field(min_length=1, max_length=200)
    # Optional only for custom endpoints, which are sometimes self-hosted without a key.
    api_key: str = Field(default="", max_length=4000)
    base_url: str | None = Field(default=None, max_length=500)
    azure_api_version: str | None = Field(default=None, max_length=40)
    # The owner confirmed the notice that answering questions sends project data to this provider.
    accept_data_notice: bool


class AiConversationRead(BaseModel):
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AiConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class AiQuestionCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class AiChatMessageRead(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    # Assistant answers only.
    status: Literal["QUEUED", "RUNNING", "DONE", "FAILED"] | None = None
    progress: str | None = None
    # The lookups it made, e.g. "Looking up tasks".
    steps: list[str] = []
    error: str | None = None


class AiConversationDetail(AiConversationRead):
    messages: list[AiChatMessageRead]
    active_run_id: UUID | None
