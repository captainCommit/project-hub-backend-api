from datetime import date, datetime
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
    platform: "AiPlatformInfo"
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


# Written reports ---------------------------------------------------------------------------------------

ReportScopeValue = Literal["PROJECT", "PROGRAM", "PORTFOLIO"]
ReportTypeValue = Literal["weekly_status", "steering_committee", "executive_summary", "raid", "health", "changes"]


class AiReportCreate(BaseModel):
    scope_type: ReportScopeValue
    scope_id: UUID
    report_type: ReportTypeValue
    # Start of the period the report covers (default: a week ago).
    since: date | None = None
    # Write a fresh one even if the same report was written a few minutes ago.
    regenerate: bool = False


class AiReportRead(BaseModel):
    id: UUID
    scope_type: ReportScopeValue
    scope_id: UUID
    scope_name: str
    report_type: str
    title: str
    since: date
    status: Literal["QUEUED", "RUNNING", "DONE", "FAILED"]
    progress: str | None = None
    error: str | None = None
    # Markdown; only on a finished report, and not in lists.
    content: str | None = None
    model: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


# Platform-provided models -------------------------------------------------------------------------------

PlatformProviderValue = Literal["OPENAI", "ANTHROPIC", "GEMINI"]
PlatformAccessStatusValue = Literal["REQUESTED", "ENABLED", "DISABLED", "DECLINED"]
AllowanceKindValue = Literal["TOKENS", "CREDIT"]


class AiUsageRead(BaseModel):
    month_start: datetime
    tokens: int
    charge_cents: float
    billable_cents: float
    allowance_kind: AllowanceKindValue
    allowance_amount: int
    allowance_left: int
    cap_cents: int | None
    can_bill: bool
    limit_reached: bool


class AiPlatformInfo(BaseModel):
    """What an owner sees about the platform model for their account."""

    status: PlatformAccessStatusValue | None
    model_label: str | None
    requested_at: datetime | None
    usage: AiUsageRead | None
    currency: str


class AiPlatformRequest(BaseModel):
    # The owner accepts that usage past the allowance is added to the bill and that data goes to the provider.
    accept_terms: bool


class AiPlatformModelRead(BaseModel):
    id: UUID
    label: str
    provider: PlatformProviderValue
    model: str
    input_cost_per_mtok_cents: float
    output_cost_per_mtok_cents: float
    margin_percent: float
    is_active: bool
    # Whether the platform's key for this provider is configured on the server.
    key_configured: bool = False

    model_config = ConfigDict(from_attributes=True)


class AiPlatformModelCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    provider: PlatformProviderValue
    model: str = Field(min_length=1, max_length=200)
    input_cost_per_mtok_cents: float = Field(ge=0)
    output_cost_per_mtok_cents: float = Field(ge=0)
    margin_percent: float = Field(default=30, ge=0, le=1000)
    is_active: bool = True


class AiPlatformModelUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    input_cost_per_mtok_cents: float | None = Field(default=None, ge=0)
    output_cost_per_mtok_cents: float | None = Field(default=None, ge=0)
    margin_percent: float | None = Field(default=None, ge=0, le=1000)
    is_active: bool | None = None


class AiPlatformAccountRead(BaseModel):
    """One account's platform AI, for platform admins: terms and usage only, never conversations."""

    account_id: UUID
    account_name: str
    status: PlatformAccessStatusValue
    platform_model_id: UUID | None
    model_label: str | None
    allowance_kind: AllowanceKindValue
    allowance_amount: int
    monthly_cap_cents: int | None
    has_own_key: bool
    requested_at: datetime | None
    requested_by_email: str | None
    decided_at: datetime | None
    usage: AiUsageRead


class AiPlatformAccessUpdate(BaseModel):
    status: Literal["ENABLED", "DISABLED", "DECLINED"]
    platform_model_id: UUID | None = None
    allowance_kind: AllowanceKindValue | None = None
    allowance_amount: int | None = Field(default=None, ge=0)
    monthly_cap_cents: int | None = Field(default=None, ge=0)
    # Explicitly remove the cap (monthly_cap_cents=null alone means "unchanged").
    clear_cap: bool = False


class AiPlatformDefaults(BaseModel):
    allowance_kind: AllowanceKindValue
    allowance_amount: int
    currency: str


AiSettingsRead.model_rebuild()
