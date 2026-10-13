"""Per-seat billing through Stripe.

Each billed account has one Stripe subscription with up to two items: full seats (owners, managers,
members) and viewer seats. A seat is an active member who has signed in; pending invites are free.
Seat counts are pushed to Stripe as they change, and Stripe prorates. Stripe webhooks keep the
subscription status, billing period and a copy of each invoice up to date here.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.account import Account
from app.models.account_member import AccountMember, AccountMemberRole
from app.models.billing import LOCKED_SUBSCRIPTION_STATUSES, AccountBilling, Invoice, StripeEvent
from app.models.user import User
from app.services.access import access_for
from app.services.platform_audit import PlatformAuditAction, record_platform_action
from app.services.stripe_gateway import StripeGateway, StripeNotConfigured


logger = logging.getLogger(__name__)

# Subscriptions that are still running; anything else needs a fresh checkout.
LIVE_SUBSCRIPTION_STATUSES = {"active", "trialing", "past_due", "incomplete"}

BILLING_NOT_ENABLED = "Billing isn't turned on for this account."
STRIPE_NOT_CONFIGURED = "Payments aren't set up yet. Please try again later."


def from_timestamp(value: object) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=UTC) if isinstance(value, (int, float)) else None


class BillingService:
    def __init__(self, db: Session, *, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    # Seats

    def seat_counts(self, account_id: UUID) -> tuple[int, int, int]:
        """(full seats, viewer seats, pending invites) for one account."""
        rows = self.db.execute(
            select(AccountMember.role, User.cognito_sub.is_not(None), User.is_active, func.count())
            .join(User, User.id == AccountMember.user_id)
            .where(AccountMember.account_id == account_id)
            .group_by(AccountMember.role, User.cognito_sub.is_not(None), User.is_active)
        ).all()
        full = viewer = pending = 0
        for role, signed_in, active, count in rows:
            if not signed_in:
                pending += count
            elif active:
                if role == AccountMemberRole.VIEWER.value:
                    viewer += count
                else:
                    full += count
        return full, viewer, pending

    def monthly_total_cents(self, *, full_seats: int, viewer_seats: int) -> int:
        return full_seats * self.settings.seat_price_cents + viewer_seats * self.settings.viewer_seat_price_cents

    def sync_seats(self, account_id: UUID) -> bool:
        """Push the account's seat counts to its Stripe subscription. Returns whether anything changed."""
        billing = self.db.get(AccountBilling, account_id)
        if (
            billing is None
            or not billing.billing_enabled
            or not billing.stripe_subscription_id
            or billing.subscription_status not in LIVE_SUBSCRIPTION_STATUSES
        ):
            return False
        full, viewer, _pending = self.seat_counts(account_id)
        items: list[dict[str, Any]] = []
        if billing.full_seat_item_id and full != billing.billed_full_seats:
            items.append({"id": billing.full_seat_item_id, "quantity": full})
        if viewer and billing.viewer_seat_item_id and viewer != billing.billed_viewer_seats:
            items.append({"id": billing.viewer_seat_item_id, "quantity": viewer})
        elif viewer and not billing.viewer_seat_item_id:
            items.append({"price": self.settings.stripe_viewer_seat_price_id, "quantity": viewer})
        elif not viewer and billing.viewer_seat_item_id:
            items.append({"id": billing.viewer_seat_item_id, "deleted": True})
        if not items:
            return False
        subscription = self.gateway().update_subscription_items(billing.stripe_subscription_id, items)
        self.apply_subscription(billing, subscription)
        billing.seats_synced_at = datetime.now(UTC)
        return True

    def sync_seats_quietly(self, account_ids: list[UUID]) -> None:
        """After a seat change: try to tell Stripe, but never fail the change itself.

        A miss is corrected by the next sync, at the latest when Stripe announces the upcoming invoice.
        """
        for account_id in account_ids:
            try:
                if self.sync_seats(account_id):
                    self.db.commit()
            except StripeNotConfigured:
                return
            except Exception:
                self.db.rollback()
                logger.exception("Stripe seat sync failed", extra={"account_id": str(account_id)})

    def sync_seats_for_user(self, user_id: UUID) -> None:
        account_ids = list(self.db.scalars(select(AccountMember.account_id).where(AccountMember.user_id == user_id)))
        self.sync_seats_quietly(account_ids)

    # Owners: overview, checkout and the Stripe billing portal

    def overview(self, *, account_id: UUID, current_user: User) -> dict[str, object]:
        self.require_owner(account_id, current_user)
        billing = self.db.get(AccountBilling, account_id)
        full, viewer, pending = self.seat_counts(account_id)
        invoices = self.db.scalars(
            select(Invoice).where(Invoice.account_id == account_id).order_by(Invoice.issued_at.desc().nulls_last())
        ).all()
        return {
            **self.state(billing),
            "currency": self.settings.billing_currency,
            "seat_price_cents": self.settings.seat_price_cents,
            "viewer_seat_price_cents": self.settings.viewer_seat_price_cents,
            "full_seats": full,
            "viewer_seats": viewer,
            "pending_invites": pending,
            "estimated_monthly_cents": self.monthly_total_cents(full_seats=full, viewer_seats=viewer),
            "subscription_status": billing.subscription_status if billing else None,
            "current_period_end": billing.current_period_end if billing else None,
            "cancel_at_period_end": bool(billing and billing.cancel_at_period_end),
            "has_payment_account": bool(billing and billing.stripe_customer_id),
            "invoices": list(invoices),
            "ai_usage": self.ai_usage(account_id),
        }

    def ai_usage(self, account_id: UUID) -> dict[str, object] | None:
        from app.services.ai.platform import PlatformAiService

        return PlatformAiService(self.db, self.settings).owner_view(account_id)["usage"]  # type: ignore[return-value]

    def start_checkout(self, *, account_id: UUID, current_user: User) -> str:
        self.require_owner(account_id, current_user)
        billing = self.db.get(AccountBilling, account_id)
        if billing is None or not billing.billing_enabled:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=BILLING_NOT_ENABLED)
        if billing.stripe_subscription_id and billing.subscription_status in LIVE_SUBSCRIPTION_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This account already has a subscription. Use Manage billing to change it.",
            )
        gateway = self.checkout_gateway()
        account = self.db.get(Account, account_id)
        assert account is not None
        if not billing.stripe_customer_id:
            billing.stripe_customer_id = gateway.create_customer(
                name=account.name, email=current_user.email, account_id=str(account_id)
            )
            self.db.commit()
        full, viewer, _pending = self.seat_counts(account_id)
        line_items = [{"price": self.settings.stripe_full_seat_price_id, "quantity": max(full, 1)}]
        if viewer:
            line_items.append({"price": self.settings.stripe_viewer_seat_price_id, "quantity": viewer})
        if self.settings.stripe_ai_usage_price_id:
            # Metered: platform AI usage past the free allowance (nothing for accounts that don't use it).
            line_items.append({"price": self.settings.stripe_ai_usage_price_id})
        return gateway.create_checkout_session(
            customer_id=billing.stripe_customer_id,
            account_id=str(account_id),
            line_items=line_items,
            success_url=f"{self.settings.app_url}/Account?tab=billing&checkout=success",
            cancel_url=f"{self.settings.app_url}/Account?tab=billing&checkout=cancelled",
        )

    def open_portal(self, *, account_id: UUID, current_user: User) -> str:
        self.require_owner(account_id, current_user)
        billing = self.db.get(AccountBilling, account_id)
        if billing is None or not billing.stripe_customer_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Set up billing first.")
        return self.checkout_gateway().create_portal_session(
            customer_id=billing.stripe_customer_id,
            return_url=f"{self.settings.app_url}/Account?tab=billing",
        )

    # Platform admins

    def set_billing_enabled(self, *, account_id: UUID, enabled: bool, actor: User) -> AccountBilling:
        account = self.db.get(Account, account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        billing = self.get_or_create(account_id)
        if billing.billing_enabled != enabled:
            billing.billing_enabled = enabled
            record_platform_action(
                self.db,
                actor=actor,
                action=PlatformAuditAction.ACCOUNT_BILLING_ENABLED if enabled else PlatformAuditAction.ACCOUNT_BILLING_DISABLED,
                account=account,
            )
        self.db.commit()
        self.db.refresh(billing)
        return billing

    # Stripe webhooks

    def handle_webhook(self, *, payload: bytes, signature: str) -> str:
        gateway = self.gateway_or_503()
        try:
            event = gateway.construct_event(payload, signature)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe signature.") from exc
        if self.db.get(StripeEvent, event["id"]) is not None:
            return "duplicate"

        event_type: str = event["type"]
        obj: dict[str, Any] = event["data"]["object"]
        if event_type == "checkout.session.completed":
            self.on_checkout_completed(obj, gateway)
        elif event_type.startswith("customer.subscription."):
            self.on_subscription_changed(obj, deleted=event_type == "customer.subscription.deleted")
        elif event_type == "invoice.upcoming":
            # Last chance to get seat counts and AI usage right before Stripe bills.
            billing = self.billing_for_customer(obj.get("customer"))
            if billing is not None:
                self.sync_seats(billing.account_id)
                self.report_ai_usage(billing.account_id, gateway)
        elif event_type.startswith("invoice."):
            self.on_invoice(obj)
        self.db.add(StripeEvent(id=event["id"], type=event_type))
        self.db.commit()
        return "processed"

    def on_checkout_completed(self, session: dict[str, Any], gateway: StripeGateway) -> None:
        account_id = session.get("client_reference_id")
        subscription_id = session.get("subscription")
        if not account_id or not subscription_id:
            return
        billing = self.get_or_create(UUID(account_id))
        billing.stripe_customer_id = session.get("customer") or billing.stripe_customer_id
        billing.stripe_subscription_id = subscription_id
        self.apply_subscription(billing, gateway.retrieve_subscription(subscription_id))
        # Seats may have changed while the owner was on the checkout page.
        self.sync_seats(billing.account_id)

    def on_subscription_changed(self, subscription: dict[str, Any], *, deleted: bool) -> None:
        billing = self.db.scalar(
            select(AccountBilling).where(AccountBilling.stripe_subscription_id == subscription.get("id"))
        )
        if billing is None:
            account_id = (subscription.get("metadata") or {}).get("account_id")
            billing = self.db.get(AccountBilling, UUID(account_id)) if account_id else None
            if billing is None:
                return
            billing.stripe_subscription_id = subscription.get("id")
        self.apply_subscription(billing, subscription)
        if deleted:
            billing.subscription_status = "canceled"

    def on_invoice(self, data: dict[str, Any]) -> None:
        billing = self.billing_for_customer(data.get("customer"))
        if billing is None or not data.get("id"):
            return
        invoice = self.db.scalar(select(Invoice).where(Invoice.stripe_invoice_id == data["id"]))
        if invoice is None:
            invoice = Invoice(account_id=billing.account_id, stripe_invoice_id=data["id"])
            self.db.add(invoice)
        invoice.number = data.get("number")
        invoice.status = data.get("status")
        invoice.currency = (data.get("currency") or self.settings.billing_currency).upper()
        invoice.total = int(data.get("total") or 0)
        invoice.amount_due = int(data.get("amount_due") or 0)
        invoice.amount_paid = int(data.get("amount_paid") or 0)
        invoice.amount_remaining = int(data.get("amount_remaining") or 0)
        invoice.period_start = from_timestamp(data.get("period_start"))
        invoice.period_end = from_timestamp(data.get("period_end"))
        invoice.hosted_invoice_url = data.get("hosted_invoice_url")
        invoice.invoice_pdf = data.get("invoice_pdf")
        invoice.issued_at = from_timestamp(data.get("created"))

    def apply_subscription(self, billing: AccountBilling, subscription: dict[str, Any]) -> None:
        billing.subscription_status = subscription.get("status")
        billing.cancel_at_period_end = bool(subscription.get("cancel_at_period_end"))
        items = (subscription.get("items") or {}).get("data") or []
        # Newer Stripe API versions keep the billing period on each item rather than the subscription.
        period_source = subscription if subscription.get("current_period_end") else (items[0] if items else {})
        billing.current_period_start = from_timestamp(period_source.get("current_period_start"))
        billing.current_period_end = from_timestamp(period_source.get("current_period_end"))
        billing.full_seat_item_id = billing.viewer_seat_item_id = billing.ai_usage_item_id = None
        billing.billed_full_seats = billing.billed_viewer_seats = 0
        for item in items:
            price_id = (item.get("price") or {}).get("id")
            if price_id == self.settings.stripe_full_seat_price_id:
                billing.full_seat_item_id = item.get("id")
                billing.billed_full_seats = int(item.get("quantity") or 0)
            elif price_id == self.settings.stripe_viewer_seat_price_id:
                billing.viewer_seat_item_id = item.get("id")
                billing.billed_viewer_seats = int(item.get("quantity") or 0)
            elif price_id and price_id == self.settings.stripe_ai_usage_price_id:
                billing.ai_usage_item_id = item.get("id")

    def report_ai_usage(self, account_id: UUID, gateway: StripeGateway) -> None:
        from app.services.ai.usage_billing import AiUsageReporter

        try:
            AiUsageReporter(self.db, self.settings, gateway=gateway).report_account(account_id)
        except Exception:
            self.db.rollback()
            logger.exception("AI usage report failed", extra={"account_id": str(account_id)})

    def ensure_ai_usage_item(self, account_id: UUID) -> bool:
        """Add the metered AI usage line to a live subscription that doesn't have it yet (subscribed before AI
        existed). Returns whether the subscription now has it."""
        billing = self.db.get(AccountBilling, account_id)
        if billing is None or billing.ai_usage_item_id:
            return bool(billing and billing.ai_usage_item_id)
        if (
            not self.settings.stripe_ai_usage_price_id
            or not billing.billing_enabled
            or not billing.stripe_subscription_id
            or billing.subscription_status not in LIVE_SUBSCRIPTION_STATUSES
        ):
            return False
        subscription = self.gateway().add_subscription_item(
            billing.stripe_subscription_id, self.settings.stripe_ai_usage_price_id
        )
        self.apply_subscription(billing, subscription)
        self.db.commit()
        return bool(billing.ai_usage_item_id)

    # Helpers

    @staticmethod
    def state(billing: AccountBilling | None) -> dict[str, bool]:
        """What the UI needs for banners."""
        enabled = bool(billing and billing.billing_enabled)
        status_value = billing.subscription_status if billing else None
        return {
            "billing_enabled": enabled,
            "needs_setup": enabled and status_value not in LIVE_SUBSCRIPTION_STATUSES and status_value not in LOCKED_SUBSCRIPTION_STATUSES,
            "past_due": enabled and status_value == "past_due",
            "read_only": bool(billing and billing.is_read_only),
        }

    def require_owner(self, account_id: UUID, current_user: User) -> None:
        if not access_for(self.db, account_id=account_id, user=current_user).is_owner:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only account owners manage billing.")

    def get_or_create(self, account_id: UUID) -> AccountBilling:
        billing = self.db.get(AccountBilling, account_id)
        if billing is None:
            billing = AccountBilling(account_id=account_id)
            self.db.add(billing)
            self.db.flush()
        return billing

    def billing_for_customer(self, customer_id: object) -> AccountBilling | None:
        if not customer_id:
            return None
        return self.db.scalar(select(AccountBilling).where(AccountBilling.stripe_customer_id == str(customer_id)))

    def gateway(self) -> StripeGateway:
        return StripeGateway(self.settings)

    def checkout_gateway(self) -> StripeGateway:
        """Checkout and the portal also need an absolute URL for Stripe to send people back to."""
        if not self.settings.app_url.startswith("http"):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=STRIPE_NOT_CONFIGURED)
        return self.gateway_or_503()

    def gateway_or_503(self) -> StripeGateway:
        try:
            return self.gateway()
        except StripeNotConfigured as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=STRIPE_NOT_CONFIGURED) from exc
