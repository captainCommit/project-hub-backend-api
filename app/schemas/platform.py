from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SeatCounts(BaseModel):
    OWNER: int = 0
    MANAGER: int = 0
    MEMBER: int = 0
    VIEWER: int = 0


class AccountBillingLine(BaseModel):
    account_id: UUID
    name: str
    slug: str
    created_at: datetime
    seats: SeatCounts
    # Seats billed at the full rate (every role but VIEWER) and at the viewer rate.
    full_seats: int
    viewer_seats: int
    pending_invites: int
    monthly_total_cents: int
    billing_enabled: bool
    subscription_status: str | None
    past_due: bool
    read_only: bool
    # Unpaid balance on open Stripe invoices.
    outstanding_cents: int


class BillingSummary(BaseModel):
    currency: str
    seat_price_cents: int
    viewer_seat_price_cents: int
    accounts: list[AccountBillingLine]
    full_seats: int
    viewer_seats: int
    pending_invites: int
    monthly_total_cents: int


class SystemStatus(BaseModel):
    environment: str
    auth_mode: str
    database: str
    database_latency_ms: float | None
    migration_version: str | None
    account_count: int
    user_count: int
    signed_in_user_count: int
    checked_at: datetime


class PlatformAuditEventRead(BaseModel):
    id: UUID
    actor_email: str
    action: str
    account_id: UUID | None
    account_name: str | None
    target: str | None
    details: dict[str, object] | None
    request_id: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
