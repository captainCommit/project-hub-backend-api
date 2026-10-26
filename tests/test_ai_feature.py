from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account_member import AccountMemberRole
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.user import User
from app.services.ai.runner import resolve_model
from tests.ai_support import FakeModel, acting_as, add_member, client, create_account, db_session, dev_user  # noqa: F401
from tests.ai_support import ai_settings, fake_model  # noqa: F401

OPENAI_KEY = {"provider": "OPENAI", "model": "gpt-test", "api_key": "sk-test-1234567890abcd", "accept_data_notice": True}


def url(account: dict, path: str = "") -> str:
    return f"/api/v1/accounts/{account['account']['id']}/ai{path}"


def switch(client: TestClient, account: dict, enabled: bool):
    return client.put(f"/api/v1/admin/ai/feature/{account['account']['id']}", json={"enabled": enabled})


def test_ai_is_off_until_a_platform_admin_turns_it_on(client: TestClient, db_session: Session) -> None:
    account = create_account(client, ai=False)

    status = client.get(url(account, "/status")).json()
    assert (status["available"], status["reason"], status["enabled"]) == (False, "NOT_ENABLED", False)
    # Nothing AI works: no settings tab, no chats, no platform model requests.
    assert client.get(url(account, "/settings")).status_code == 403
    assert client.post(url(account, "/conversations"), json={"question": "How are we doing?"}).status_code == 403
    assert client.post(url(account, "/platform-request"), json={"accept_terms": True}).status_code == 403

    assert switch(client, account, True).status_code == 200
    assert client.get(url(account, "/status")).json()["reason"] == "NOT_SET_UP"
    assert client.get(url(account, "/settings")).status_code == 200
    event = db_session.scalars(select(PlatformAuditEvent).where(PlatformAuditEvent.action == "account.ai_enabled")).one()
    assert str(event.account_id) == account["account"]["id"]


def test_owners_request_ai_and_platform_admins_see_the_request(client: TestClient, db_session: Session) -> None:
    account = create_account(client, ai=False)
    create_account(client, "Globex", ai=False)
    member = add_member(db_session, account, "mia@example.com", modules=["TASKS"])

    with acting_as(member):
        assert client.post(url(account, "/access-request")).status_code == 403
    requested = client.post(url(account, "/access-request"))
    assert requested.status_code == 200, requested.text
    assert requested.json()["requested_at"] is not None

    queue = client.get("/api/v1/admin/ai/feature").json()
    assert queue[0]["account_id"] == account["account"]["id"]  # requests come first
    assert (queue[0]["enabled"], queue[0]["requested_by_email"]) == (False, "dev@example.com")

    switched = switch(client, account, True).json()
    assert (switched["enabled"], switched["requested_at"]) == (True, None)


def test_only_platform_admins_switch_ai(client: TestClient, db_session: Session) -> None:
    account = create_account(client, ai=False)
    owner = add_member(db_session, account, "olga@example.com", modules=[], role=AccountMemberRole.OWNER)
    with acting_as(owner):
        assert switch(client, account, True).status_code == 403
        assert client.get("/api/v1/admin/ai/feature").status_code == 403


def test_switching_ai_off_stops_the_assistant_even_with_a_key(
    client: TestClient, db_session: Session, fake_model: FakeModel, ai_settings
) -> None:
    account = create_account(client)
    fake_model.tool_call("ping", '{"ok": true}')
    assert client.put(url(account, "/key"), json=OPENAI_KEY).status_code == 200
    account_id = UUID(account["account"]["id"])
    assert resolve_model(db_session, ai_settings, account_id) is not None

    switch(client, account, False)
    status = client.get(url(account, "/status")).json()
    assert (status["available"], status["reason"]) == (False, "NOT_ENABLED")
    # The worker won't use the saved key either.
    assert resolve_model(db_session, ai_settings, account_id) is None
