"""Create the Stripe product and the two monthly seat prices billing uses, and print their IDs.

Run once per Stripe mode (test, then live). Reads STRIPE_SECRET_KEY from .env or the environment,
and the amounts and currency from the API settings (SEAT_PRICE_CENTS, VIEWER_SEAT_PRICE_CENTS,
BILLING_CURRENCY):
    python scripts/setup_stripe_prices.py

Put the printed IDs in STRIPE_FULL_SEAT_PRICE_ID and STRIPE_VIEWER_SEAT_PRICE_ID (.env locally,
GitHub `dev` environment variables when deployed). Stripe prices can't be edited: to change an amount,
run this again and switch to the new IDs; existing subscriptions keep their old price until moved.
"""

from __future__ import annotations

import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import stripe  # noqa: E402

from app.core.config import Settings  # noqa: E402


def main() -> int:
    settings = Settings()
    if not settings.stripe_secret_key:
        print("Set STRIPE_SECRET_KEY first (a test key starts with sk_test_).", file=sys.stderr)
        return 1
    currency = settings.billing_currency.lower()
    product = stripe.Product.create(
        api_key=settings.stripe_secret_key,
        name="Project Hub seats",
        description="Per-seat monthly subscription. Viewers are billed at the viewer rate.",
    )
    prices = {}
    for key, nickname, amount in (
        ("STRIPE_FULL_SEAT_PRICE_ID", "Full seat (owner, manager, member)", settings.seat_price_cents),
        ("STRIPE_VIEWER_SEAT_PRICE_ID", "Viewer seat", settings.viewer_seat_price_cents),
    ):
        price = stripe.Price.create(
            api_key=settings.stripe_secret_key,
            product=product.id,
            nickname=nickname,
            currency=currency,
            unit_amount=amount,
            recurring={"interval": "month"},
            tax_behavior="exclusive",
        )
        prices[key] = price.id
    for key, price_id in prices.items():
        print(f"{key}={price_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
