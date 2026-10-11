from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BillingState(BaseModel):
    """Enough for banners: whether the account is billed, needs a card, is behind, or is locked."""

    billing_enabled: bool
    needs_setup: bool
    past_due: bool
    read_only: bool


class InvoiceRead(BaseModel):
    id: UUID
    account_id: UUID
    number: str | None
    status: str | None
    currency: str
    total: int
    amount_due: int
    amount_paid: int
    amount_remaining: int
    period_start: datetime | None
    period_end: datetime | None
    hosted_invoice_url: str | None
    invoice_pdf: str | None
    issued_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AccountBillingRead(BillingState):
    currency: str
    seat_price_cents: int
    viewer_seat_price_cents: int
    full_seats: int
    viewer_seats: int
    pending_invites: int
    estimated_monthly_cents: int
    subscription_status: str | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    has_payment_account: bool
    invoices: list[InvoiceRead]


class RedirectRead(BaseModel):
    url: str


class BillingToggle(BaseModel):
    billing_enabled: bool
