from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import app
from app.models.ai import AiPlatformAccess, AiRun
from app.models.billing import AccountBilling
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.user import User
from app.routers.ai import get_ai_dispatcher
from app.services.ai.runner import AssistantRunner
from app.services.ai.usage import platform_usage
from tests.ai_support import PUBLIC_RESOLVER, FakeModel, acting_as, add_member, client, create_account, db_session  # noqa: F401
from tests.ai_support import fake_model  # noqa: F401

MODEL = {
    "label": "OpenAI standard",
    "provider": "OPENAI",
    "model": "gpt-platform",
    "input_cost_per_mtok_cents": 250,
    "output_cost_per_mtok_cents": 1000,
    "margin_percent": 20,
}


@pytest.fixture()
def ai_settings() -> Settings:
    return Settings(
        auth_mode="local",
        environment="local",
        ai_local_key_secret="test-secret",
        ai_platform_openai_api_key="sk-platform-key",
        ai_default_allowance_amount=10_000,
    )


@pytest.fixture()
def admin(db_session: Session) -> User:
    user = User(email="prjadmin@projecthub.com", full_name="Platform Admin", is_platform_admin=True)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture()
def answers(db_session: Session, ai_settings: Settings, fake_model: FakeModel, client: TestClient) -> None:
    def dispatch(run_id: UUID, background: object) -> None:
        AssistantRunner(db_session, ai_settings, transport=fake_model, resolver=PUBLIC_RESOLVER).run(run_id)

    app.dependency_overrides[get_ai_dispatcher] = lambda: dispatch


@pytest.fixture()
def account(client: TestClient) -> dict:
    return create_account(client)


@pytest.fixture()
def platform_model(client: TestClient, admin: User) -> dict:
    with acting_as(admin):
        response = client.post("/api/v1/admin/ai/models", json=MODEL)
    assert response.status_code == 201, response.text
    return response.json()


def account_url(account: dict, path: str) -> str:
    return f"/api/v1/accounts/{account['account']['id']}/ai{path}"


def request_platform(client: TestClient, account: dict):
    return client.post(account_url(account, "/platform-request"), json={"accept_terms": True})


def decide(client: TestClient, admin: User, account: dict, **body: object):
    with acting_as(admin):
        return client.put(f"/api/v1/admin/ai/accounts/{account['account']['id']}", json=body)


def enable(client: TestClient, admin: User, account: dict, platform_model: dict, **terms: object) -> dict:
    assert request_platform(client, account).status_code == 200
    response = decide(client, admin, account, status="ENABLED", platform_model_id=platform_model["id"], **terms)
    assert response.status_code == 200, response.text
    return response.json()


def ask(client: TestClient, account: dict, text: str = "What's late?"):
    conversation = client.post(account_url(account, "/conversations"), json={}).json()
    return client.post(f"/api/v1/ai/conversations/{conversation['id']}/messages", json={"content": text})


def test_owner_requests_and_admin_switches_it_on(client: TestClient, db_session: Session, account: dict, admin: User, platform_model: dict) -> None:
    no_terms = client.post(account_url(account, "/platform-request"), json={"accept_terms": False})
    unrequested = decide(client, admin, account, status="ENABLED", platform_model_id=platform_model["id"])
    requested = request_platform(client, account)
    with acting_as(admin):
        queue = client.get("/api/v1/admin/ai/accounts").json()
    enabled = decide(client, admin, account, status="ENABLED", platform_model_id=platform_model["id"], allowance_kind="CREDIT", allowance_amount=500)
    viewer = add_member(db_session, account, "vic@example.com", modules=[])
    with acting_as(viewer):
        status = client.get(account_url(account, "/status")).json()

    assert no_terms.status_code == 400
    assert unrequested.status_code == 404
    assert requested.json()["platform"]["status"] == "REQUESTED"
    assert [(row["status"], row["requested_by_email"]) for row in queue] == [("REQUESTED", "dev@example.com")]
    assert queue[0]["allowance_amount"] == 10_000  # the configured default
    assert enabled.status_code == 200 and enabled.json()["status"] == "ENABLED"
    assert (enabled.json()["allowance_kind"], enabled.json()["allowance_amount"]) == ("CREDIT", 500)
    assert status == {
        "available": True, "source": "PLATFORM", "reason": None, "provider_label": "OpenAI", "model": "gpt-platform", "can_manage": False,
    }
    actions = db_session.scalars(select(PlatformAuditEvent.action)).all()
    assert "ai.platform_model_created" in actions and "account.ai_platform_enabled" in actions


def test_platform_model_answers_with_the_platform_key_and_charges(
    client: TestClient, db_session: Session, account: dict, admin: User, platform_model: dict, fake_model: FakeModel, answers: None
) -> None:
    enable(client, admin, account, platform_model)
    fake_model.reply("Nothing is late.", usage=(1000, 100))

    answer = ask(client, account).json()["messages"][-1]

    assert answer["status"] == "DONE"
    assert fake_model.calls[0]["headers"]["Authorization"] == "Bearer sk-platform-key"
    assert fake_model.calls[0]["body"]["model"] == "gpt-platform"
    run = db_session.scalars(select(AiRun)).one()
    assert run.source == "PLATFORM" and run.platform_model_id == UUID(platform_model["id"])
    # (1000 x 250 + 100 x 1000) / 1M cents, plus 20%.
    assert run.charge_cents == Decimal("0.42")


def test_own_key_takes_precedence(client: TestClient, account: dict, admin: User, platform_model: dict, fake_model: FakeModel) -> None:
    enable(client, admin, account, platform_model)
    fake_model.tool_call("ping", '{"ok": true}')
    saved = client.put(account_url(account, "/key"), json={"provider": "OPENAI", "model": "gpt-own", "api_key": "sk-own-12345678", "accept_data_notice": True})

    status = client.get(account_url(account, "/status")).json()

    assert saved.status_code == 200, saved.text
    assert (status["source"], status["model"]) == ("BYOK", "gpt-own")


def test_token_allowance_is_the_limit_when_usage_cannot_be_billed(
    client: TestClient, db_session: Session, account: dict, admin: User, platform_model: dict, fake_model: FakeModel, answers: None
) -> None:
    enable(client, admin, account, platform_model, allowance_kind="TOKENS", allowance_amount=1000)
    fake_model.reply("First answer.", usage=(1100, 100))

    first = ask(client, account, "First")
    status = client.get(account_url(account, "/status")).json()
    second = ask(client, account, "Second")

    assert first.json()["messages"][-1]["status"] == "DONE"
    assert (status["available"], status["reason"]) == (False, "LIMIT_REACHED")
    assert second.status_code == 409 and "allowance" in second.text
    usage = client.get(account_url(account, "/settings")).json()["platform"]["usage"]
    assert (usage["tokens"], usage["allowance_left"], usage["can_bill"], usage["limit_reached"]) == (1200, 0, False, True)


def test_billable_usage_stops_at_the_cap(client: TestClient, db_session: Session, account: dict, admin: User, platform_model: dict, ai_settings: Settings) -> None:
    access_terms = enable(client, admin, account, platform_model, allowance_kind="CREDIT", allowance_amount=100, monthly_cap_cents=500)
    ai_settings.stripe_ai_usage_price_id = "price_ai"
    account_id = UUID(account["account"]["id"])
    db_session.add(AccountBilling(account_id=account_id, billing_enabled=True, subscription_status="active", ai_usage_item_id="si_ai"))
    for charge in ("80", "300"):
        db_session.add(AiRun(account_id=account_id, status="DONE", source="PLATFORM", input_tokens=10, output_tokens=0, charge_cents=Decimal(charge)))
    db_session.commit()
    access = db_session.get(AiPlatformAccess, account_id)

    under_cap = platform_usage(db_session, ai_settings, access)
    db_session.add(AiRun(account_id=account_id, status="DONE", source="PLATFORM", input_tokens=10, output_tokens=0, charge_cents=Decimal("400")))
    db_session.commit()
    over_cap = platform_usage(db_session, ai_settings, access)

    assert access_terms["monthly_cap_cents"] == 500
    assert (under_cap.can_bill, under_cap.billable_cents, under_cap.limit_reached) == (True, Decimal("280"), False)
    assert (over_cap.billable_cents, over_cap.limit_reached) == (Decimal("680"), True)


def test_token_allowance_is_taken_in_order(db_session: Session, client: TestClient, account: dict, ai_settings: Settings) -> None:
    account_id = UUID(account["account"]["id"])
    access = AiPlatformAccess(account_id=account_id, status="ENABLED", allowance_kind="TOKENS", allowance_amount=1500)
    db_session.add(access)
    early = datetime(2026, 10, 2, tzinfo=UTC)
    db_session.add(AiRun(account_id=account_id, status="DONE", source="PLATFORM", input_tokens=1000, output_tokens=0, charge_cents=Decimal("10"), created_at=early))
    db_session.add(AiRun(account_id=account_id, status="DONE", source="PLATFORM", input_tokens=1000, output_tokens=0, charge_cents=Decimal("20"), created_at=early.replace(day=3)))
    db_session.add(AiRun(account_id=account_id, status="DONE", source="BYOK", input_tokens=5000, output_tokens=0, charge_cents=Decimal("0"), created_at=early))
    db_session.add(AiRun(account_id=account_id, status="DONE", source="PLATFORM", input_tokens=9000, output_tokens=0, charge_cents=Decimal("90"), created_at=datetime(2026, 9, 30, tzinfo=UTC)))
    db_session.commit()

    usage = platform_usage(db_session, ai_settings, access, now=datetime(2026, 10, 20, tzinfo=UTC))

    # First run free; half of the second (500 of 1000 tokens) is billable. Own-key runs and last month don't count.
    assert (usage.tokens, usage.charge_cents, usage.billable_cents, usage.allowance_left) == (2000, Decimal("30"), Decimal("10"), 0)


def test_owner_can_stop_and_must_ask_again(client: TestClient, account: dict, admin: User, platform_model: dict) -> None:
    enable(client, admin, account, platform_model)

    stopped = client.delete(account_url(account, "/platform-request")).json()
    re_enable = decide(client, admin, account, status="ENABLED", platform_model_id=platform_model["id"])

    assert stopped["platform"]["status"] == "DISABLED"
    assert client.get(account_url(account, "/status")).json()["reason"] == "NOT_SET_UP"
    assert re_enable.status_code == 409 and "has to request" in re_enable.text


def test_admin_endpoints_are_for_platform_admins(client: TestClient, db_session: Session, account: dict, admin: User, platform_model: dict) -> None:
    member = add_member(db_session, account, "alex@example.com", modules=["TASKS"])

    with acting_as(member):
        refused = [client.get("/api/v1/admin/ai/models"), client.post("/api/v1/admin/ai/models", json=MODEL), client.get("/api/v1/admin/ai/accounts")]
    with acting_as(member):
        member_request = client.post(account_url(account, "/platform-request"), json={"accept_terms": True})
    with acting_as(admin):
        updated = client.patch(f"/api/v1/admin/ai/models/{platform_model['id']}", json={"margin_percent": 35, "is_active": False})

    assert [r.status_code for r in refused] == [403, 403, 403]
    assert member_request.status_code == 403
    assert updated.json()["margin_percent"] == 35 and updated.json()["is_active"] is False
    assert updated.json()["key_configured"] is True


def test_owner_billing_shows_ai_usage(client: TestClient, account: dict, admin: User, platform_model: dict) -> None:
    enable(client, admin, account, platform_model)

    billing = client.get(f"/api/v1/accounts/{account['account']['id']}/billing").json()

    assert billing["ai_usage"]["allowance_amount"] == 10_000 and billing["ai_usage"]["tokens"] == 0
