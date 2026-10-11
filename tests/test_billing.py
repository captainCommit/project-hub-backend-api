"""Per-seat billing through Stripe, with Stripe itself replaced by a recording fake."""

import json
from collections.abc import Generator
from datetime import UTC
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.services.billing as billing_service
from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.account_member import AccountMember
from app.models.billing import AccountBilling, Invoice
from app.models.user import User
from app.services.auth import get_current_user

FULL_PRICE = "price_full"
VIEWER_PRICE = "price_viewer"
PERIOD_START, PERIOD_END = 1_790_000_000, 1_792_592_000


class FakeStripe:
    """Stands in for StripeGateway: records calls and keeps one subscription per id."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.subscriptions: dict[str, dict[str, Any]] = {}

    def __call__(self, settings: Settings) -> "FakeStripe":  # constructed like StripeGateway(settings)
        if not settings.stripe_enabled:
            raise billing_service.StripeNotConfigured
        return self

    def create_customer(self, *, name: str, email: str | None, account_id: str) -> str:
        self.calls.append(("create_customer", name))
        return "cus_1"

    def create_checkout_session(self, **kwargs: Any) -> str:
        self.calls.append(("checkout", kwargs["line_items"]))
        return "https://checkout.stripe.test/session"

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        return "https://billing.stripe.test/portal"

    def retrieve_subscription(self, subscription_id: str) -> dict[str, Any]:
        return self.subscriptions[subscription_id]

    def update_subscription_items(self, subscription_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append(("update_items", items))
        subscription = self.subscriptions[subscription_id]
        current = subscription["items"]["data"]
        for change in items:
            if change.get("deleted"):
                current[:] = [i for i in current if i["id"] != change["id"]]
            elif "id" in change:
                next(i for i in current if i["id"] == change["id"])["quantity"] = change["quantity"]
            else:
                current.append({"id": f"si_{change['price']}", "price": {"id": change["price"]}, "quantity": change["quantity"]})
        return subscription

    def construct_event(self, payload: bytes, signature: str) -> dict[str, Any]:
        if signature != "good":
            raise ValueError("bad signature")
        return json.loads(payload)


def subscription(sub_id: str = "sub_1", status: str = "active", full: int = 1) -> dict[str, Any]:
    item = {
        "id": "si_full",
        "price": {"id": FULL_PRICE},
        "quantity": full,
        "current_period_start": PERIOD_START,
        "current_period_end": PERIOD_END,
    }
    return {"id": sub_id, "status": status, "cancel_at_period_end": False, "items": {"data": [item]}, "metadata": {}}


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=Session, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def stripe_settings() -> Settings:
    return Settings(
        auth_mode="local",
        stripe_secret_key="sk_test_x",
        stripe_webhook_secret="whsec_x",
        stripe_full_seat_price_id=FULL_PRICE,
        stripe_viewer_seat_price_id=VIEWER_PRICE,
    )


@pytest.fixture()
def fake_stripe(monkeypatch: pytest.MonkeyPatch) -> FakeStripe:
    fake = FakeStripe()
    monkeypatch.setattr(billing_service, "StripeGateway", fake)
    return fake


@pytest.fixture()
def client(db_session: Session, stripe_settings: Settings, fake_stripe: FakeStripe) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: stripe_settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def account(client: TestClient) -> dict:
    return client.post("/api/v1/accounts", json={"name": "Acme", "slug": "acme"}).json()


def send_event(client: TestClient, event_id: str, event_type: str, obj: dict[str, Any], signature: str = "good"):
    payload = {"id": event_id, "type": event_type, "data": {"object": obj}}
    return client.post("/api/v1/billing/stripe-webhook", content=json.dumps(payload), headers={"Stripe-Signature": signature})


def subscribe(client: TestClient, fake_stripe: FakeStripe, account_id: str) -> None:
    """Billing on, checkout, and Stripe confirming the subscription."""
    assert client.patch(f"/api/v1/admin/accounts/{account_id}/billing", json={"billing_enabled": True}).status_code == 200
    assert client.post(f"/api/v1/accounts/{account_id}/billing/checkout").status_code == 200
    fake_stripe.subscriptions["sub_1"] = subscription()
    response = send_event(
        client,
        "evt_checkout",
        "checkout.session.completed",
        {"client_reference_id": account_id, "customer": "cus_1", "subscription": "sub_1"},
    )
    assert response.json() == {"status": "processed"}


def add_member(db_session: Session, account_id: str, email: str, role: str, *, signed_in: bool = True) -> User:
    user = User(email=email, cognito_sub=f"sub-{email}" if signed_in else None)
    db_session.add(user)
    db_session.flush()
    db_session.add(AccountMember(account_id=UUID(account_id), user_id=user.id, role=role))
    db_session.commit()
    return user


def test_billing_is_off_until_a_platform_admin_turns_it_on(client: TestClient, account: dict) -> None:
    overview = client.get(f"/api/v1/accounts/{account['id']}/billing").json()
    assert (overview["billing_enabled"], overview["needs_setup"], overview["currency"]) == (False, False, "CAD")

    refused = client.post(f"/api/v1/accounts/{account['id']}/billing/checkout")
    assert (refused.status_code, refused.json()["message"]) == (409, "Billing isn't turned on for this account.")

    assert client.patch(f"/api/v1/admin/accounts/{account['id']}/billing", json={"billing_enabled": True}).json() == {
        "billing_enabled": True,
        "needs_setup": True,
        "past_due": False,
        "read_only": False,
    }
    actions = [e["action"] for e in client.get("/api/v1/admin/audit").json()["items"]]
    assert actions[0] == "account.billing_enabled"


def test_checkout_subscribes_with_current_seats_and_webhook_records_it(
    client: TestClient, db_session: Session, fake_stripe: FakeStripe, account: dict
) -> None:
    add_member(db_session, account["id"], "v@acme.example", "VIEWER")
    add_member(db_session, account["id"], "pending@acme.example", "MEMBER", signed_in=False)

    subscribe(client, fake_stripe, account["id"])

    # The local dev owner never signs in through Cognito, so checkout bills at least one full seat.
    assert ("checkout", [{"price": FULL_PRICE, "quantity": 1}, {"price": VIEWER_PRICE, "quantity": 1}]) in fake_stripe.calls
    billing = db_session.get(AccountBilling, UUID(account["id"]))
    assert (billing.stripe_customer_id, billing.stripe_subscription_id, billing.subscription_status) == ("cus_1", "sub_1", "active")
    # SQLite drops the time zone; the value is stored in UTC.
    assert billing.current_period_end is not None
    assert int(billing.current_period_end.replace(tzinfo=UTC).timestamp()) == PERIOD_END
    # The fake subscription came back with only full seats, so the viewer seat was added straight after.
    assert ("update_items", [{"id": "si_full", "quantity": 0}, {"price": VIEWER_PRICE, "quantity": 1}]) in fake_stripe.calls
    assert (billing.billed_full_seats, billing.billed_viewer_seats) == (0, 1)


def test_seat_changes_reach_stripe(client: TestClient, db_session: Session, fake_stripe: FakeStripe, account: dict) -> None:
    subscribe(client, fake_stripe, account["id"])
    fake_stripe.calls.clear()
    returning = User(email="returning@acme.example", cognito_sub="sub-returning")
    db_session.add(returning)
    db_session.commit()

    # Someone who has signed in before takes a seat as soon as they are added.
    client.post(f"/api/v1/accounts/{account['id']}/users/invite", json={"email": "returning@acme.example", "role": "MEMBER"})
    billing = db_session.get(AccountBilling, UUID(account["id"]))
    assert billing.billed_full_seats == 1

    # Moving them to a viewer seat moves the charge too.
    client.post(
        f"/api/v1/accounts/{account['id']}/users/invite",
        json={"email": "returning@acme.example", "role": "VIEWER", "update_existing": True},
    )
    assert (billing.billed_full_seats, billing.billed_viewer_seats) == (0, 1)
    # A brand-new invite is pending and changes nothing.
    calls = len(fake_stripe.calls)
    client.post(f"/api/v1/accounts/{account['id']}/users/invite", json={"email": "new@acme.example", "role": "MEMBER"})
    assert len(fake_stripe.calls) == calls


def test_invoices_are_recorded_once(client: TestClient, db_session: Session, fake_stripe: FakeStripe, account: dict) -> None:
    subscribe(client, fake_stripe, account["id"])
    invoice = {
        "id": "in_1",
        "customer": "cus_1",
        "number": "ACME-0001",
        "status": "paid",
        "currency": "cad",
        "total": 3000,
        "amount_due": 3000,
        "amount_paid": 3000,
        "amount_remaining": 0,
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "created": PERIOD_START,
        "hosted_invoice_url": "https://invoice.stripe.test/in_1",
        "invoice_pdf": "https://invoice.stripe.test/in_1.pdf",
    }

    assert send_event(client, "evt_inv", "invoice.paid", invoice).json() == {"status": "processed"}
    assert send_event(client, "evt_inv", "invoice.paid", invoice).json() == {"status": "duplicate"}

    [listed] = client.get(f"/api/v1/accounts/{account['id']}/billing").json()["invoices"]
    assert (listed["number"], listed["status"], listed["currency"], listed["total"]) == ("ACME-0001", "paid", "CAD", 3000)
    assert db_session.scalar(select(Invoice).where(Invoice.stripe_invoice_id == "in_1")) is not None
    assert client.get("/api/v1/admin/invoices").json()["total"] == 1


def test_unpaid_accounts_are_read_only(client: TestClient, fake_stripe: FakeStripe, account: dict) -> None:
    portfolio = client.post(f"/api/v1/accounts/{account['id']}/portfolios", json={"name": "P"}).json()
    program = client.post(f"/api/v1/portfolios/{portfolio['id']}/programs", json={"name": "G"}).json()
    project = client.post(f"/api/v1/programs/{program['id']}/projects", json={"name": "J"}).json()
    subscribe(client, fake_stripe, account["id"])

    send_event(client, "evt_past_due", "customer.subscription.updated", subscription(status="past_due"))
    access = client.get(f"/api/v1/accounts/{account['id']}/my-access").json()["billing"]
    assert (access["past_due"], access["read_only"]) == (True, False)
    assert client.post(f"/api/v1/projects/{project['id']}/tasks", json={"name": "Still fine"}).status_code == 201

    send_event(client, "evt_unpaid", "customer.subscription.updated", subscription(status="unpaid"))
    assert client.get(f"/api/v1/accounts/{account['id']}/my-access").json()["billing"]["read_only"] is True
    blocked = client.post(f"/api/v1/projects/{project['id']}/tasks", json={"name": "Blocked"})
    assert blocked.status_code == 402
    assert client.post(f"/api/v1/accounts/{account['id']}/portfolios", json={"name": "Blocked"}).status_code == 402
    assert client.get(f"/api/v1/projects/{project['id']}/tasks").status_code == 200
    # Paying (or a platform admin switching billing off) unlocks it.
    send_event(client, "evt_paid", "customer.subscription.updated", subscription(status="active"))
    assert client.post(f"/api/v1/projects/{project['id']}/tasks", json={"name": "Back"}).status_code == 201


def test_webhooks_need_a_valid_signature(client: TestClient) -> None:
    response = send_event(client, "evt_x", "invoice.paid", {"id": "in_x"}, signature="forged")

    assert (response.status_code, response.json()["message"]) == (400, "Invalid Stripe signature.")


def test_only_owners_see_or_change_billing(client: TestClient, db_session: Session, account: dict) -> None:
    manager = add_member(db_session, account["id"], "boss@acme.example", "MANAGER")
    app.dependency_overrides[get_current_user] = lambda: manager
    try:
        assert client.get(f"/api/v1/accounts/{account['id']}/billing").status_code == 403
        assert client.post(f"/api/v1/accounts/{account['id']}/billing/portal").status_code == 403
        assert client.patch(f"/api/v1/admin/accounts/{account['id']}/billing", json={"billing_enabled": True}).status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_checkout_without_stripe_configured(client: TestClient, stripe_settings: Settings, account: dict) -> None:
    client.patch(f"/api/v1/admin/accounts/{account['id']}/billing", json={"billing_enabled": True})
    stripe_settings.stripe_secret_key = ""

    response = client.post(f"/api/v1/accounts/{account['id']}/billing/checkout")

    assert (response.status_code, response.json()["message"]) == (503, "Payments aren't set up yet. Please try again later.")


def test_platform_billing_summary_shows_subscription_state(client: TestClient, fake_stripe: FakeStripe, account: dict) -> None:
    subscribe(client, fake_stripe, account["id"])
    send_event(client, "evt_open", "invoice.finalized", {"id": "in_2", "customer": "cus_1", "status": "open", "currency": "cad", "amount_remaining": 2000})

    [line] = client.get("/api/v1/admin/billing").json()["accounts"]

    assert (line["billing_enabled"], line["subscription_status"], line["outstanding_cents"]) == (True, "active", 2000)
