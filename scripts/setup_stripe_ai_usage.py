"""Create the Stripe billing meter and metered price that platform AI usage is billed with, and print the price ID.

Run once per Stripe mode (test, then live), after setup_stripe_prices.py. Reads STRIPE_SECRET_KEY,
BILLING_CURRENCY and STRIPE_AI_METER_EVENT_NAME from .env or the environment:
    python scripts/setup_stripe_ai_usage.py

Put the printed ID in STRIPE_AI_USAGE_PRICE_ID (.env locally, GitHub `dev` environment variable when deployed).
One unit is one cent: the API works out each account's billable usage (model cost plus margin, after the free
allowance) and reports the cents, so prices and allowances can change without touching Stripe.
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
    meter = stripe.billing.Meter.create(
        api_key=settings.stripe_secret_key,
        display_name="Project Hub AI usage (cents)",
        event_name=settings.stripe_ai_meter_event_name,
        default_aggregation={"formula": "sum"},
        customer_mapping={"type": "by_id", "event_payload_key": "stripe_customer_id"},
        value_settings={"event_payload_key": "value"},
    )
    product = stripe.Product.create(
        api_key=settings.stripe_secret_key,
        name="Project Hub AI assistant",
        description="Usage of Project Hub's AI model past the monthly free allowance.",
    )
    price = stripe.Price.create(
        api_key=settings.stripe_secret_key,
        product=product.id,
        nickname="AI usage (1 unit = 1 cent)",
        currency=settings.billing_currency.lower(),
        unit_amount=1,
        recurring={"interval": "month", "usage_type": "metered", "meter": meter.id},
        tax_behavior="exclusive",
    )
    print(f"STRIPE_AI_USAGE_PRICE_ID={price.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
