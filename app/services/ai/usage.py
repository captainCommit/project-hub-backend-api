"""This month's platform AI usage for an account, and what of it is billable.

Only runs answered with a platform model count; an account's own key is billed by its provider. Each run stores
its charge (the model's price plus margin), so changing a price never rewrites past usage. The monthly allowance
is then taken off, in one of two forms:
- TOKENS: the first N tokens of the month are free, in the order the runs happened.
- CREDIT: the first N cents of charges are free.

Usage is counted per calendar month in UTC.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.ai import AiPlatformAccess, AiRun, AllowanceKind
from app.models.billing import AccountBilling

ZERO = Decimal("0")
# Stripe statuses where a metered line will actually be invoiced.
BILLABLE_SUBSCRIPTION_STATUSES = {"active", "trialing", "past_due"}


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


@dataclass(frozen=True)
class MonthUsage:
    month_start: datetime
    tokens: int
    # Before the allowance.
    charge_cents: Decimal
    # After the allowance: what goes on the invoice.
    billable_cents: Decimal
    allowance_kind: str
    allowance_amount: int
    # Tokens or cents still free this month.
    allowance_left: int
    cap_cents: int | None
    # Whether usage past the allowance can be invoiced (a live subscription with the AI usage line).
    can_bill: bool
    limit_reached: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "month_start": self.month_start,
            "tokens": self.tokens,
            "charge_cents": float(round(self.charge_cents, 2)),
            "billable_cents": float(round(self.billable_cents, 2)),
            "allowance_kind": self.allowance_kind,
            "allowance_amount": self.allowance_amount,
            "allowance_left": self.allowance_left,
            "cap_cents": self.cap_cents,
            "can_bill": self.can_bill,
            "limit_reached": self.limit_reached,
        }


def can_bill_usage(db: Session, settings: Settings, account_id: UUID) -> bool:
    billing = db.get(AccountBilling, account_id)
    return bool(
        settings.stripe_ai_usage_price_id
        and billing is not None
        and billing.billing_enabled
        and billing.subscription_status in BILLABLE_SUBSCRIPTION_STATUSES
        and billing.ai_usage_item_id
    )


def platform_usage(
    db: Session, settings: Settings, access: AiPlatformAccess, *, now: datetime | None = None
) -> MonthUsage:
    start = month_start(now)
    runs = db.execute(
        select(AiRun.input_tokens + AiRun.output_tokens, AiRun.charge_cents)
        .where(AiRun.account_id == access.account_id, AiRun.source == "PLATFORM", AiRun.created_at >= start)
        .order_by(AiRun.created_at, AiRun.id)
    ).all()
    tokens = sum(int(t or 0) for t, _ in runs)
    charge = sum((Decimal(c or 0) for _, c in runs), ZERO)

    if access.allowance_kind == AllowanceKind.CREDIT.value:
        billable = max(ZERO, charge - access.allowance_amount)
        left = int(max(ZERO, access.allowance_amount - charge))
    else:
        free_left = access.allowance_amount
        billable = ZERO
        for run_tokens, run_charge in runs:
            run_tokens = int(run_tokens or 0)
            if run_tokens <= 0:
                continue
            free = min(free_left, run_tokens)
            free_left -= free
            billable += Decimal(run_charge or 0) * (run_tokens - free) / run_tokens
        left = free_left

    can_bill = can_bill_usage(db, settings, access.account_id)
    if not can_bill:
        # Nothing past the allowance could be invoiced, so the allowance is the limit.
        limit_reached = billable > 0 or left <= 0
    else:
        limit_reached = access.monthly_cap_cents is not None and billable >= access.monthly_cap_cents
    return MonthUsage(
        month_start=start,
        tokens=tokens,
        charge_cents=charge,
        billable_cents=billable,
        allowance_kind=access.allowance_kind,
        allowance_amount=access.allowance_amount,
        allowance_left=int(left),
        cap_cents=access.monthly_cap_cents,
        can_bill=can_bill,
        limit_reached=limit_reached,
    )
