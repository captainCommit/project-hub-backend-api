"""Reporting platform AI usage to Stripe, so it appears on the account's next invoice.

Hourly (and when Stripe announces an upcoming invoice), each account's billable usage for the month is worked
out (app.services.ai.usage) and only the whole cents not yet reported are sent, as a billing meter event on the
subscription's metered AI usage line (1 unit = 1 cent). Each event's identifier names the account, month and new
running total, so a retry after a failure can never bill twice.

Early in a month the previous month is checked once more, so its last hour of usage isn't lost.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.ai import AiPlatformAccess, AiRun, AiUsageReport
from app.models.billing import AccountBilling
from app.services.ai.usage import can_bill_usage, month_start, next_month_start, platform_usage
from app.services.billing import BillingService
from app.services.stripe_gateway import StripeGateway, StripeNotConfigured

logger = logging.getLogger(__name__)

# How long into a month the previous one is still checked for unreported usage.
PREVIOUS_MONTH_GRACE = timedelta(days=3)


class AiUsageReporter:
    def __init__(self, db: Session, settings: Settings, *, gateway: StripeGateway | None = None) -> None:
        self.db = db
        self.settings = settings
        self._gateway = gateway

    def gateway(self) -> StripeGateway:
        if self._gateway is None:
            self._gateway = StripeGateway(self.settings)
        return self._gateway

    def report_all(self, *, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        counts = {"accounts": 0, "cents": 0, "failed": 0}
        if not self.settings.stripe_ai_usage_price_id:
            return counts
        months = [month_start(now)]
        if now - months[0] < PREVIOUS_MONTH_GRACE:
            months.append(month_start(months[0] - timedelta(days=1)))
        for start in months:
            account_ids = self.db.scalars(
                select(AiRun.account_id)
                .where(AiRun.source == "PLATFORM", AiRun.created_at >= start, AiRun.created_at < next_month_start(start))
                .distinct()
            ).all()
            for account_id in account_ids:
                try:
                    sent = self.report_account(account_id, month=start)
                except StripeNotConfigured:
                    return counts
                except Exception:
                    self.db.rollback()
                    counts["failed"] += 1
                    logger.exception("AI usage report failed", extra={"account_id": str(account_id)})
                    continue
                if sent:
                    counts["accounts"] += 1
                    counts["cents"] += sent
        return counts

    def report_account(self, account_id: UUID, *, month: datetime | None = None) -> int:
        """Send this account's not-yet-reported billable cents for the month. Returns how many were sent."""
        start = month or month_start()
        access = self.db.get(AiPlatformAccess, account_id)
        billing = self.db.get(AccountBilling, account_id)
        if access is None or billing is None or not billing.stripe_customer_id:
            return 0
        if not can_bill_usage(self.db, self.settings, account_id):
            # Subscribed before AI usage existed: add the metered line, then try again.
            BillingService(self.db, settings=self.settings).ensure_ai_usage_item(account_id)
            if not can_bill_usage(self.db, self.settings, account_id):
                return 0
        usage = platform_usage(self.db, self.settings, access, now=start)
        target = int(usage.billable_cents)  # whole cents; the fraction waits for the next report
        report = self.db.get(AiUsageReport, (account_id, start))
        if report is None:
            report = AiUsageReport(account_id=account_id, month_start=start, reported_cents=0)
            self.db.add(report)
        delta = target - report.reported_cents
        if delta <= 0:
            # Usage can only fall if an allowance was raised after it was reported; nothing is taken back.
            self.db.commit()
            return 0
        self.gateway().report_usage(
            event_name=self.settings.stripe_ai_meter_event_name,
            customer_id=billing.stripe_customer_id,
            value=delta,
            identifier=f"ai-usage-{account_id}-{start:%Y%m}-{target}",
        )
        report.reported_cents = target
        self.db.commit()
        return delta
