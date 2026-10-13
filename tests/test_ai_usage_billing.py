import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.ai import AiPlatformAccess, AiRun, AiUsageReport
from app.models.billing import AccountBilling
from app.services import billing as billing_service
from app.services.ai import usage_billing
from app.services.ai.usage_billing import AiUsageReporter
from app.services.billing import BillingService
from tests.ai_support import client, create_account, db_session, fake_model  # noqa: F401
from tests.test_billing import FULL_PRICE, VIEWER_PRICE, FakeStripe, subscription

AI_PRICE = "price_ai_usage"
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


class FakeStripeWithUsage(FakeStripe):
    def __init__(self) -> None:
        super().__init__()
        self.usage: list[dict[str, Any]] = []

    def add_subscription_item(self, subscription_id: str, price_id: str) -> dict[str, Any]:
        self.calls.append(("add_item", price_id))
        sub = self.subscriptions[subscription_id]
        sub["items"]["data"].append({"id": "si_ai", "price": {"id": price_id}})
        return sub

    def report_usage(self, *, event_name: str, customer_id: str, value: int, identifier: str) -> None:
        self.usage.append({"event_name": event_name, "customer_id": customer_id, "value": value, "identifier": identifier})


@pytest.fixture()
def ai_settings() -> Settings:
    return Settings(
        auth_mode="local",
        environment="local",
        stripe_secret_key="sk_test_x",
        stripe_webhook_secret="whsec_x",
        stripe_full_seat_price_id=FULL_PRICE,
        stripe_viewer_seat_price_id=VIEWER_PRICE,
        stripe_ai_usage_price_id=AI_PRICE,
    )


@pytest.fixture()
def stripe(monkeypatch: pytest.MonkeyPatch) -> FakeStripeWithUsage:
    fake = FakeStripeWithUsage()
    monkeypatch.setattr(billing_service, "StripeGateway", fake)
    monkeypatch.setattr(usage_billing, "StripeGateway", fake)
    return fake


@pytest.fixture()
def account_id(client: TestClient, db_session: Session, stripe: FakeStripeWithUsage) -> UUID:
    account_id = UUID(create_account(client)["account"]["id"])
    db_session.add(AiPlatformAccess(account_id=account_id, status="ENABLED", allowance_kind="CREDIT", allowance_amount=100))
    sub = subscription()
    sub["items"]["data"].append({"id": "si_ai", "price": {"id": AI_PRICE}})
    stripe.subscriptions["sub_1"] = sub
    db_session.add(
        AccountBilling(
            account_id=account_id,
            billing_enabled=True,
            stripe_customer_id="cus_1",
            stripe_subscription_id="sub_1",
            subscription_status="active",
            ai_usage_item_id="si_ai",
        )
    )
    db_session.commit()
    return account_id


def add_run(db_session: Session, account_id: UUID, charge: str, when: datetime = NOW) -> None:
    db_session.add(
        AiRun(account_id=account_id, status="DONE", source="PLATFORM", input_tokens=100, output_tokens=10, charge_cents=Decimal(charge), created_at=when)
    )
    db_session.commit()


def test_only_new_whole_cents_are_reported(db_session: Session, ai_settings: Settings, account_id: UUID, stripe: FakeStripeWithUsage) -> None:
    reporter = AiUsageReporter(db_session, ai_settings)
    add_run(db_session, account_id, "80")
    add_run(db_session, account_id, "300")

    first = reporter.report_all(now=NOW)
    again = reporter.report_all(now=NOW)
    add_run(db_session, account_id, "50.7")
    later = reporter.report_all(now=NOW)

    # 380 charged, 100 free: 280 billable; then 330.7, of which the whole 50 new cents are sent.
    assert first == {"accounts": 1, "cents": 280, "failed": 0}
    assert again["cents"] == 0
    assert later["cents"] == 50
    assert [(u["value"], u["identifier"]) for u in stripe.usage] == [
        (280, f"ai-usage-{account_id}-202610-280"),
        (50, f"ai-usage-{account_id}-202610-330"),
    ]
    assert stripe.usage[0]["customer_id"] == "cus_1" and stripe.usage[0]["event_name"] == "project_hub_ai_usage"
    assert db_session.get(AiUsageReport, (account_id, datetime(2026, 10, 1, tzinfo=UTC))).reported_cents == 330


def test_older_subscriptions_get_the_usage_line(db_session: Session, ai_settings: Settings, account_id: UUID, stripe: FakeStripeWithUsage) -> None:
    billing = db_session.get(AccountBilling, account_id)
    billing.ai_usage_item_id = None
    stripe.subscriptions["sub_1"]["items"]["data"].pop()  # subscribed before AI existed
    db_session.commit()
    add_run(db_session, account_id, "150")

    AiUsageReporter(db_session, ai_settings).report_all(now=NOW)

    assert ("add_item", AI_PRICE) in stripe.calls
    assert db_session.get(AccountBilling, account_id).ai_usage_item_id == "si_ai"
    assert [u["value"] for u in stripe.usage] == [50]


def test_nothing_is_reported_without_a_live_subscription(db_session: Session, ai_settings: Settings, account_id: UUID, stripe: FakeStripeWithUsage) -> None:
    db_session.get(AccountBilling, account_id).subscription_status = "canceled"
    db_session.commit()
    add_run(db_session, account_id, "500")

    counts = AiUsageReporter(db_session, ai_settings).report_all(now=NOW)

    assert counts["cents"] == 0 and stripe.usage == []


def test_previous_month_is_finished_early_in_the_next(db_session: Session, ai_settings: Settings, account_id: UUID, stripe: FakeStripeWithUsage) -> None:
    add_run(db_session, account_id, "250", when=datetime(2026, 9, 30, 23, 50, tzinfo=UTC))

    early = AiUsageReporter(db_session, ai_settings).report_all(now=datetime(2026, 10, 1, 0, 30, tzinfo=UTC))
    late = AiUsageReporter(db_session, ai_settings).report_all(now=datetime(2026, 10, 10, tzinfo=UTC))

    assert early["cents"] == 150 and stripe.usage[0]["identifier"].endswith("-202609-150")
    assert late["cents"] == 0


def test_no_reports_without_the_ai_price(db_session: Session, ai_settings: Settings, account_id: UUID, stripe: FakeStripeWithUsage) -> None:
    ai_settings.stripe_ai_usage_price_id = ""
    add_run(db_session, account_id, "500")

    assert AiUsageReporter(db_session, ai_settings).report_all(now=NOW) == {"accounts": 0, "cents": 0, "failed": 0}


def test_checkout_and_subscriptions_include_the_usage_line(db_session: Session, ai_settings: Settings, account_id: UUID, stripe: FakeStripeWithUsage) -> None:
    service = BillingService(db_session, settings=ai_settings)
    billing = db_session.get(AccountBilling, account_id)
    billing.ai_usage_item_id = None

    service.apply_subscription(billing, stripe.subscriptions["sub_1"])

    assert billing.ai_usage_item_id == "si_ai"


def test_checkout_adds_the_metered_line(client: TestClient, db_session: Session, ai_settings: Settings, stripe: FakeStripeWithUsage) -> None:
    account_id = UUID(create_account(client)["account"]["id"])
    db_session.add(AccountBilling(account_id=account_id, billing_enabled=True))
    db_session.commit()

    response = client.post(f"/api/v1/accounts/{account_id}/billing/checkout")

    assert response.status_code == 200, response.text
    [(_, line_items)] = [call for call in stripe.calls if call[0] == "checkout"]
    assert {"price": AI_PRICE} in line_items


def test_upcoming_invoice_reports_usage(client: TestClient, db_session: Session, account_id: UUID, stripe: FakeStripeWithUsage) -> None:
    db_session.add(AiRun(account_id=account_id, status="DONE", source="PLATFORM", input_tokens=10, output_tokens=0, charge_cents=Decimal("140")))
    db_session.commit()
    event = {"id": "evt_upcoming", "type": "invoice.upcoming", "data": {"object": {"customer": "cus_1"}}}

    response = client.post("/api/v1/billing/stripe-webhook", content=json.dumps(event), headers={"Stripe-Signature": "good"})

    assert response.status_code == 200
    assert [u["value"] for u in stripe.usage] == [40]
