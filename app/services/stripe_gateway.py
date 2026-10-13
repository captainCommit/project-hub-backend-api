"""The few Stripe calls billing makes, behind one small class so tests can stand in for Stripe."""

from typing import Any

from app.core.config import Settings


class StripeNotConfigured(Exception):
    """No Stripe secret key or seat prices are configured."""


class StripeGateway:
    def __init__(self, settings: Settings) -> None:
        if not settings.stripe_enabled:
            raise StripeNotConfigured
        import stripe

        self.stripe = stripe
        self.api_key = settings.stripe_secret_key
        self.webhook_secret = settings.stripe_webhook_secret

    def create_customer(self, *, name: str, email: str | None, account_id: str) -> str:
        customer = self.stripe.Customer.create(
            api_key=self.api_key, name=name, email=email, metadata={"account_id": account_id}
        )
        return customer.id

    def create_checkout_session(
        self,
        *,
        customer_id: str,
        account_id: str,
        line_items: list[dict[str, Any]],
        success_url: str,
        cancel_url: str,
    ) -> str:
        session = self.stripe.checkout.Session.create(
            api_key=self.api_key,
            mode="subscription",
            customer=customer_id,
            client_reference_id=account_id,
            line_items=line_items,
            subscription_data={"metadata": {"account_id": account_id}},
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return session.url

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        session = self.stripe.billing_portal.Session.create(
            api_key=self.api_key, customer=customer_id, return_url=return_url
        )
        return session.url

    def retrieve_subscription(self, subscription_id: str) -> dict[str, Any]:
        return self.stripe.Subscription.retrieve(subscription_id, api_key=self.api_key).to_dict()

    def update_subscription_items(self, subscription_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Change seat quantities; Stripe prorates the difference onto the next invoice."""
        return self.stripe.Subscription.modify(
            subscription_id,
            api_key=self.api_key,
            items=items,
            proration_behavior="create_prorations",
        ).to_dict()

    def add_subscription_item(self, subscription_id: str, price_id: str) -> dict[str, Any]:
        """Add a line (the metered AI usage price) to a subscription. Nothing to prorate: it is billed by usage."""
        return self.stripe.Subscription.modify(
            subscription_id,
            api_key=self.api_key,
            items=[{"price": price_id}],
            proration_behavior="none",
        ).to_dict()

    def report_usage(self, *, event_name: str, customer_id: str, value: int, identifier: str) -> None:
        """Send a billing meter event. Stripe ignores a repeated identifier, so a retry never double-bills."""
        self.stripe.billing.MeterEvent.create(
            api_key=self.api_key,
            event_name=event_name,
            payload={"stripe_customer_id": customer_id, "value": str(value)},
            identifier=identifier,
        )

    def construct_event(self, payload: bytes, signature: str) -> dict[str, Any]:
        """Verify a webhook's signature and parse it. Raises ValueError or a signature error if invalid."""
        event = self.stripe.Webhook.construct_event(payload, signature, self.webhook_secret)
        return event.to_dict()
