from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, String, Uuid, func
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
