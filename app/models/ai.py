from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

AI_PROVIDERS = ("OPENAI", "ANTHROPIC", "GEMINI", "AZURE_OPENAI", "CUSTOM")
DEFAULT_RETENTION_DAYS = 90


class AiAccountSettings(Base):
    """An account's assistant settings, managed by its owners: on/off, chat retention, and its own model key."""

    __tablename__ = "ai_account_settings"
    __table_args__ = (
        CheckConstraint(
            "provider is null or provider in (" + ", ".join(f"'{p}'" for p in AI_PROVIDERS) + ")",
            name="ck_ai_account_settings_provider",
        ),
        CheckConstraint("retention_days >= 1 and retention_days <= 3650", name="ck_ai_account_settings_retention_days"),
    )

    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    # The owner's switch. Off means nobody in the account can use the assistant, whatever model is set up.
    assistant_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # Conversations untouched for longer than this are deleted.
    retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_RETENTION_DAYS, server_default=str(DEFAULT_RETENTION_DAYS)
    )

    # Bring your own key. The key is only ever stored encrypted (app.services.ai.keys).
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # For Azure OpenAI, the deployment name.
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    azure_api_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    encrypted_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_last4: Mapped[str | None] = mapped_column(String(8), nullable=True)
    key_updated_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    key_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The owner confirmed that answering questions sends project data to this provider.
    data_notice_accepted_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    data_notice_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def has_key(self) -> bool:
        return bool(self.provider and self.model and self.encrypted_key)


class AiConversation(Base):
    """One person's chat with the assistant, in one account. Nobody else can open it."""

    __tablename__ = "ai_conversations"
    __table_args__ = (Index("ix_ai_conversations_account_user_updated", "account_id", "user_id", "updated_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )
    # Bumped by every message; retention counts from here.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )


class AiRunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class AiRun(Base):
    """Answering one question: its progress, outcome, and the tokens it used (what usage billing adds up)."""

    __tablename__ = "ai_runs"
    __table_args__ = (
        CheckConstraint("status in ('QUEUED', 'RUNNING', 'DONE', 'FAILED')", name="ck_ai_runs_status"),
        CheckConstraint("source is null or source in ('BYOK', 'PLATFORM')", name="ck_ai_runs_source"),
        Index("ix_ai_runs_account_created", "account_id", "created_at"),
        Index("ix_ai_runs_user_created", "user_id", "created_at"),
        Index("ix_ai_runs_conversation", "conversation_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # Runs outlive their conversation (deleted by its owner or by retention) so usage can still be billed.
    conversation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_conversations.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AiRunStatus.QUEUED.value)
    # What it is doing right now, shown in the chat ("Looking up tasks in Apollo…").
    progress: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Platform runs only: the model used and what its tokens cost the account, margin included, before the
    # monthly allowance. Kept per run so a price change never rewrites past usage.
    platform_model_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_platform_models.id", ondelete="SET NULL"), nullable=True
    )
    charge_cents: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, default=Decimal("0"), server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_active(self) -> bool:
        return self.status in (AiRunStatus.QUEUED.value, AiRunStatus.RUNNING.value)


class AiMessage(Base):
    """One turn of a conversation in the provider-neutral shape (see app.services.ai.providers)."""

    __tablename__ = "ai_messages"
    __table_args__ = (
        CheckConstraint("role in ('user', 'assistant', 'tool')", name="ck_ai_messages_role"),
        UniqueConstraint("conversation_id", "position", name="uq_ai_messages_conversation_position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )


REPORT_SCOPES = ("PROJECT", "PROGRAM", "PORTFOLIO")


class AiReport(Base):
    """A written report (status report, health summary, what changed) on a project, program or portfolio.

    Like a conversation it belongs to the person who asked for it: it was written from what they can see, so
    nobody else can open it. Its progress, outcome and usage are on its run.
    """

    __tablename__ = "ai_reports"
    __table_args__ = (
        CheckConstraint(
            "scope_type in (" + ", ".join(f"'{s}'" for s in REPORT_SCOPES) + ")", name="ck_ai_reports_scope_type"
        ),
        Index("ix_ai_reports_user_scope_created", "user_id", "scope_type", "scope_id", "created_at"),
        Index("ix_ai_reports_account_created", "account_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # The project, program or portfolio (not a foreign key: it is one of three tables).
    scope_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    # Its name when the report was written, so the list still reads well after a rename.
    scope_name: Mapped[str] = mapped_column(String(255), nullable=False)
    report_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # Changes from this day on are what the report calls "this period".
    since: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Markdown, once written.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )


PLATFORM_PROVIDERS = ("OPENAI", "ANTHROPIC", "GEMINI")


class AiPlatformModel(Base):
    """A model the platform offers to accounts without their own key, with what it costs and what we charge.

    Costs are the provider's price in the billing currency's cents per million tokens; the charge adds margin_percent.
    The platform's keys live in SSM (/project-hub/<stage>/ai/<provider>-api-key), never in the database.
    """

    __tablename__ = "ai_platform_models"
    __table_args__ = (
        CheckConstraint(
            "provider in (" + ", ".join(f"'{p}'" for p in PLATFORM_PROVIDERS) + ")", name="ck_ai_platform_models_provider"
        ),
        CheckConstraint(
            "input_cost_per_mtok_cents >= 0 and output_cost_per_mtok_cents >= 0 and margin_percent >= 0",
            name="ck_ai_platform_models_prices",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    input_cost_per_mtok_cents: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    output_cost_per_mtok_cents: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    margin_percent: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("30"), server_default="30")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AiPlatformAccessStatus(str, Enum):
    # The owner asked for the platform model (and accepted the charges and data notice).
    REQUESTED = "REQUESTED"
    # A platform admin switched it on.
    ENABLED = "ENABLED"
    # Switched off, by a platform admin or the owner.
    DISABLED = "DISABLED"
    # A platform admin turned the request down.
    DECLINED = "DECLINED"


class AllowanceKind(str, Enum):
    # A number of free tokens each month.
    TOKENS = "TOKENS"
    # A free amount each month, in the billing currency's cents.
    CREDIT = "CREDIT"


class AiPlatformAccess(Base):
    """One account's use of a platform model: the owner's request, the admin's decision, and the monthly terms."""

    __tablename__ = "ai_platform_access"
    __table_args__ = (
        CheckConstraint(
            "status in ('REQUESTED', 'ENABLED', 'DISABLED', 'DECLINED')", name="ck_ai_platform_access_status"
        ),
        CheckConstraint("allowance_kind in ('TOKENS', 'CREDIT')", name="ck_ai_platform_access_allowance_kind"),
        CheckConstraint("allowance_amount >= 0", name="ck_ai_platform_access_allowance_amount"),
        CheckConstraint("monthly_cap_cents is null or monthly_cap_cents >= 0", name="ck_ai_platform_access_cap"),
    )

    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    platform_model_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_platform_models.id", ondelete="SET NULL"), nullable=True
    )
    allowance_kind: Mapped[str] = mapped_column(String(10), nullable=False, default=AllowanceKind.TOKENS.value)
    # Tokens or cents, depending on allowance_kind.
    allowance_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Most the account can be charged for AI in a month, after the allowance. Empty: no cap.
    monthly_cap_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AiUsageReport(Base):
    """How much of an account's platform AI usage for a month has been reported to Stripe, in whole cents.

    Each hour only the difference is sent, so a usage event is never counted twice.
    """

    __tablename__ = "ai_usage_reports"

    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    month_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    reported_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
