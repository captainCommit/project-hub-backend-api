from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.activity_log import ActivityLog
from app.models.ai import AiAccountSettings
from app.models.account_member import AccountMemberRole
from app.schemas.ai import AiKeyUpdate
from app.services.ai.account_settings import AiSettingsService
from app.services.ai.keys import LocalKeyCipher
from app.services.ai.providers import Provider
from tests.ai_support import FakeModel, acting_as, add_member, client, create_account, db_session, dev_user  # noqa: F401
from tests.ai_support import ai_settings, fake_model  # noqa: F401

OPENAI_KEY = {"provider": "OPENAI", "model": "gpt-test", "api_key": "sk-test-1234567890abcd", "accept_data_notice": True}


@pytest.fixture()
def account(client: TestClient) -> dict:
    return create_account(client)


def url(account: dict, path: str = "") -> str:
    return f"/api/v1/accounts/{account['account']['id']}/ai{path}"


def save_key(client: TestClient, account: dict, fake_model: FakeModel, **overrides: object):
    fake_model.tool_call("ping", '{"ok": true}')
    return client.put(url(account, "/key"), json={**OPENAI_KEY, **overrides})


def test_status_before_and_after_a_key_is_saved(client: TestClient, db_session: Session, account: dict, fake_model: FakeModel) -> None:
    viewer = add_member(db_session, account, "vic@example.com", modules=[], role=AccountMemberRole.VIEWER)

    with acting_as(viewer):
        before = client.get(url(account, "/status")).json()
    saved = save_key(client, account, fake_model)
    with acting_as(viewer):
        after = client.get(url(account, "/status")).json()

    assert before == {
        "available": False, "source": None, "reason": "NOT_SET_UP", "provider_label": None, "model": None, "can_manage": False,
        "enabled": True, "requested_at": None,
    }
    assert saved.status_code == 200, saved.text
    assert after == {
        "available": True, "source": "BYOK", "reason": None, "provider_label": "OpenAI", "model": "gpt-test", "can_manage": False,
        "enabled": True, "requested_at": None,
    }


def test_saved_key_is_checked_encrypted_and_never_shown(
    client: TestClient, db_session: Session, account: dict, fake_model: FakeModel, ai_settings: Settings
) -> None:
    response = save_key(client, account, fake_model)

    body = response.json()
    assert body["key"]["provider_label"] == "OpenAI" and body["key"]["key_last4"] == "abcd"
    assert body["key"]["key_updated_by_name"] == "Dev User"
    assert "sk-test" not in response.text
    # The check was a real request with the key, forcing a tool call.
    [check] = fake_model.calls
    assert check["headers"]["Authorization"] == "Bearer sk-test-1234567890abcd"
    assert check["body"]["tool_choice"]["function"]["name"] == "ping"

    account_id = UUID(account["account"]["id"])
    row = db_session.get(AiAccountSettings, account_id)
    assert b"sk-test" not in row.encrypted_key
    resolved = AiSettingsService(db_session, ai_settings).resolve_byok(account_id)
    assert resolved.config.api_key == "sk-test-1234567890abcd" and resolved.config.provider == Provider.OPENAI

    history = db_session.scalars(select(ActivityLog).where(ActivityLog.entity_type == "ACCOUNT")).all()
    assert history[-1].new_values == {"ai_model": "OpenAI · gpt-test, key ending abcd"}
    assert "sk-test" not in str([h.new_values for h in history])


def test_bad_key_is_not_saved(client: TestClient, db_session: Session, account: dict, fake_model: FakeModel) -> None:
    fake_model.fail(401, "Incorrect API key provided")

    response = client.put(url(account, "/key"), json=OPENAI_KEY)

    assert response.status_code == 400
    assert "The key didn't work: The provider rejected the API key." in response.text
    assert db_session.get(AiAccountSettings, UUID(account["account"]["id"])) is None


def test_model_without_tool_calls_is_refused(client: TestClient, account: dict, fake_model: FakeModel) -> None:
    fake_model.reply("pong")

    response = client.put(url(account, "/key"), json=OPENAI_KEY)

    assert response.status_code == 400 and "tool call" in response.text


def test_data_notice_and_endpoint_rules(client: TestClient, account: dict, fake_model: FakeModel) -> None:
    no_notice = client.put(url(account, "/key"), json={**OPENAI_KEY, "accept_data_notice": False})
    no_key = client.put(url(account, "/key"), json={**OPENAI_KEY, "api_key": ""})
    http_url = client.put(url(account, "/key"), json={**OPENAI_KEY, "provider": "CUSTOM", "base_url": "http://llm.example.com"})

    assert no_notice.status_code == 400 and "Confirm" in no_notice.text
    assert no_key.status_code == 400 and "Enter the API key" in no_key.text
    assert http_url.status_code == 400 and "https://" in http_url.text
    assert fake_model.calls == []


def test_custom_endpoint_without_key(client: TestClient, account: dict, fake_model: FakeModel) -> None:
    response = save_key(client, account, fake_model, provider="CUSTOM", model="llama", api_key="", base_url="https://llm.example.com/v1/")

    assert response.status_code == 200, response.text
    assert response.json()["key"]["base_url"] == "https://llm.example.com/v1"
    assert fake_model.calls[0]["url"] == "https://llm.example.com/v1/chat/completions"


def test_only_owners_manage_settings(client: TestClient, db_session: Session, account: dict) -> None:
    member = add_member(db_session, account, "alex@example.com", modules=["TASKS"])

    with acting_as(member):
        read = client.get(url(account, "/settings"))
        save = client.put(url(account, "/key"), json=OPENAI_KEY)
        patch = client.patch(url(account, "/settings"), json={"assistant_enabled": False})

    assert {read.status_code, save.status_code, patch.status_code} == {403}


def test_turning_off_and_removing_the_key(client: TestClient, db_session: Session, account: dict, fake_model: FakeModel) -> None:
    save_key(client, account, fake_model)

    off = client.patch(url(account, "/settings"), json={"assistant_enabled": False, "retention_days": 30})
    status_off = client.get(url(account, "/status")).json()
    client.patch(url(account, "/settings"), json={"assistant_enabled": True})
    removed = client.delete(url(account, "/key"))
    bad_retention = client.patch(url(account, "/settings"), json={"retention_days": 0})

    assert (off.json()["assistant_enabled"], off.json()["retention_days"]) == (False, 30)
    assert status_off["reason"] == "OFF" and status_off["available"] is False
    assert removed.json()["key"] is None
    assert client.get(url(account, "/status")).json()["reason"] == "NOT_SET_UP"
    assert bad_retention.status_code == 422


def test_key_storage_needs_kms_outside_local(db_session: Session, client: TestClient, account: dict) -> None:
    service = AiSettingsService(db_session, Settings(environment="dev", ai_kms_key_id=""))
    owner = dev_user(db_session)

    with pytest.raises(HTTPException) as raised:
        service.save_key(account_id=UUID(account["account"]["id"]), current_user=owner, key_in=AiKeyUpdate(**OPENAI_KEY))

    assert raised.value.status_code == 503
    assert service.read(account_id=UUID(account["account"]["id"]), current_user=owner)["key_storage_available"] is False


def test_local_cipher_binds_the_key_to_its_account() -> None:
    cipher = LocalKeyCipher("secret")
    account_id, other = uuid4(), uuid4()
    blob = cipher.encrypt(account_id, "sk-abc")

    assert cipher.decrypt(account_id, blob) == "sk-abc"
    with pytest.raises(ValueError):
        cipher.decrypt(other, blob)
