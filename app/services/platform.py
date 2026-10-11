"""Platform admin views: seats and billing, and system status.

These return account metadata and aggregates only, never an account's business data
(portfolios, programs, projects, delivery, templates and the like).
"""

import time
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.account import Account
from app.models.billing import AccountBilling, Invoice
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.schemas.platform import AccountBillingLine, BillingSummary, SeatCounts, SystemStatus
from app.services.accounts import require_platform_admin
from app.services.billing import BillingService


class PlatformService:
    def __init__(self, db: Session, *, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)

    def billing_summary(self, *, current_user: User) -> BillingSummary:
        require_platform_admin(current_user)
        seats_by_account = self.account_members.count_seats_by_account_role()
        pending_by_account = self.account_members.count_pending_invites_by_account()
        billing_by_account = {b.account_id: b for b in self.db.scalars(select(AccountBilling))}
        outstanding_by_account = dict(
            self.db.execute(
                select(Invoice.account_id, func.sum(Invoice.amount_remaining))
                .where(Invoice.status == "open")
                .group_by(Invoice.account_id)
            ).all()
        )

        lines = []
        for account in self.accounts.list_all():
            seats = SeatCounts(**seats_by_account.get(account.id, {}))
            viewer_seats = seats.VIEWER
            full_seats = sum(seats.model_dump().values()) - viewer_seats
            billing = billing_by_account.get(account.id)
            state = BillingService.state(billing)
            lines.append(
                AccountBillingLine(
                    account_id=account.id,
                    name=account.name,
                    slug=account.slug,
                    created_at=account.created_at,
                    seats=seats,
                    full_seats=full_seats,
                    viewer_seats=viewer_seats,
                    pending_invites=pending_by_account.get(account.id, 0),
                    monthly_total_cents=self.monthly_total_cents(full_seats=full_seats, viewer_seats=viewer_seats),
                    billing_enabled=state["billing_enabled"],
                    subscription_status=billing.subscription_status if billing else None,
                    past_due=state["past_due"],
                    read_only=state["read_only"],
                    outstanding_cents=int(outstanding_by_account.get(account.id) or 0),
                )
            )

        return BillingSummary(
            currency=self.settings.billing_currency,
            seat_price_cents=self.settings.seat_price_cents,
            viewer_seat_price_cents=self.settings.viewer_seat_price_cents,
            accounts=lines,
            full_seats=sum(line.full_seats for line in lines),
            viewer_seats=sum(line.viewer_seats for line in lines),
            pending_invites=sum(line.pending_invites for line in lines),
            monthly_total_cents=sum(line.monthly_total_cents for line in lines),
        )

    def monthly_total_cents(self, *, full_seats: int, viewer_seats: int) -> int:
        return full_seats * self.settings.seat_price_cents + viewer_seats * self.settings.viewer_seat_price_cents

    def system_status(self, *, current_user: User) -> SystemStatus:
        require_platform_admin(current_user)
        started = time.perf_counter()
        try:
            self.db.execute(text("SELECT 1"))
        except Exception:
            self.db.rollback()
            database, latency_ms = "disconnected", None
        else:
            database, latency_ms = "connected", round((time.perf_counter() - started) * 1000, 2)

        return SystemStatus(
            environment=self.settings.environment,
            auth_mode=self.settings.auth_mode,
            database=database,
            database_latency_ms=latency_ms,
            migration_version=self.migration_version() if database == "connected" else None,
            account_count=self.db.scalar(select(func.count()).select_from(Account)) or 0,
            user_count=self.db.scalar(select(func.count()).select_from(User)) or 0,
            signed_in_user_count=self.db.scalar(
                select(func.count()).select_from(User).where(User.cognito_sub.is_not(None))
            )
            or 0,
            checked_at=datetime.now(UTC),
        )

    def migration_version(self) -> str | None:
        try:
            return self.db.scalar(text("SELECT version_num FROM alembic_version"))
        except Exception:
            # No alembic_version table, e.g. a schema built without migrations.
            self.db.rollback()
            return None

