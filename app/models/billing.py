from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


# Stripe subscription statuses that make an account read-only once billing is on.
LOCKED_SUBSCRIPTION_STATUSES = {"unpaid", "canceled", "incomplete_expired"}


class AccountBilling(Base):
    """An account's Stripe subscription. Missing or billing_enabled=False means the account is not billed."""

    __tablename__ = "account_billing"

    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Platform admins turn billing on per account; until then the account is free.
    billing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    full_seat_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    viewer_seat_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The metered line that platform AI usage is reported to (see app.services.ai.usage).
    ai_usage_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscription_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # The quantities last sent to Stripe.
    billed_full_seats: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    billed_viewer_seats: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    seats_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def is_read_only(self) -> bool:
        return self.billing_enabled and self.subscription_status in LOCKED_SUBSCRIPTION_STATUSES


class Invoice(Base):
    """A copy of a Stripe invoice, kept up to date by webhooks. Amounts are in the currency's minor unit."""

    __tablename__ = "invoices"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stripe_invoice_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    amount_due: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    amount_paid: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    amount_remaining: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hosted_invoice_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    invoice_pdf: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class StripeEvent(Base):
    """Webhook events already handled, so Stripe's retries are processed once."""

    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )
