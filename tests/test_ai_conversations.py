import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import app
from app.models.ai import AiAccountSettings, AiConversation, AiRun
from app.routers.ai import get_ai_dispatcher
from app.services.ai.conversations import ConversationService
from app.services.ai.runner import AssistantRunner
from tests.ai_support import PUBLIC_RESOLVER, FakeModel, acting_as, add_member, client, create_account, db_session, post  # noqa: F401
from tests.ai_support import ai_settings, fake_model  # noqa: F401

KEY = {"provider": "OPENAI", "model": "gpt-test", "api_key": "sk-test-1234567890abcd", "accept_data_notice": True}


@pytest.fixture()
def answers(db_session: Session, ai_settings: Settings, fake_model: FakeModel, client: TestClient):
    """Answer each question straight away, inside the request, with the fake model."""
    dispatched: list[UUID] = []

    def dispatch(run_id: UUID, background: object) -> None:
        dispatched.append(run_id)
        AssistantRunner(db_session, ai_settings, transport=fake_model, resolver=PUBLIC_RESOLVER).run(run_id)

    app.dependency_overrides[get_ai_dispatcher] = lambda: dispatch
    return dispatched


@pytest.fixture()
def account(client: TestClient, fake_model: FakeModel) -> dict:
    hierarchy = create_account(client)
    fake_model.tool_call("ping", '{"ok": true}')
    assert client.put(f"/api/v1/accounts/{hierarchy['account']['id']}/ai/key", json=KEY).status_code == 200
    fake_model.calls.clear()
    yesterday = str(date.today() - timedelta(days=1))
    hierarchy["late"] = post(client, f"/api/v1/projects/{hierarchy['apollo']['id']}/tasks", {"name": "Late report", "finish_date": yesterday})
    return hierarchy


def start(client: TestClient, account: dict) -> dict:
    return post(client, f"/api/v1/accounts/{account['account']['id']}/ai/conversations", {})


def ask(client: TestClient, conversation: dict, text: str):
    return client.post(f"/api/v1/ai/conversations/{conversation['id']}/messages", json={"content": text})


def test_question_is_answered_with_tools(client: TestClient, db_session: Session, account: dict, fake_model: FakeModel, answers: list) -> None:
    conversation = start(client, account)
    fake_model.tool_call("list_tasks", json.dumps({"project_id": account["apollo"]["id"]}), usage=(300, 25))
    fake_model.reply("[Late report](/projects/x/sheet?task=y) is overdue.", usage=(500, 40))

    response = ask(client, conversation, "What's overdue in Apollo?")

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["title"] == "What's overdue in Apollo?"
    user, answer = body["messages"]
    assert (user["role"], user["content"]) == ("user", "What's overdue in Apollo?")
    assert answer["status"] == "DONE" and answer["content"].endswith("is overdue.")
    assert answer["steps"] == ["Looking up tasks"]
    assert body["active_run_id"] is None

    first, second = fake_model.calls
    system = first["body"]["messages"][0]["content"]
    assert "Dev User" in system and "owner" in system and account["account"]["name"] in system
    assert [t["function"]["name"] for t in first["body"]["tools"]][:2] == ["my_work", "search"]
    tool_result = second["body"]["messages"][-1]
    assert tool_result["role"] == "tool" and "Late report" in tool_result["content"]

    run = db_session.scalars(select(AiRun)).one()
    assert (run.source, run.provider, run.model) == ("BYOK", "OPENAI", "gpt-test")
    assert (run.model_calls, run.input_tokens, run.output_tokens) == (2, 800, 65)


def test_follow_up_questions_carry_the_conversation(client: TestClient, account: dict, fake_model: FakeModel, answers: list) -> None:
    conversation = start(client, account)
    fake_model.reply("Apollo has one late task.")
    ask(client, conversation, "Anything late?")
    fake_model.reply("It's the Late report.")

    ask(client, conversation, "Which one?")

    sent = [m["content"] for m in fake_model.calls[-1]["body"]["messages"][1:]]
    assert sent == ["Anything late?", "Apollo has one late task.", "Which one?"]


def test_conversations_are_private(client: TestClient, db_session: Session, account: dict) -> None:
    member = add_member(db_session, account, "alex@example.com", modules=["TASKS"])
    with acting_as(member):
        mine = start(client, account)

    owner_view = client.get(f"/api/v1/ai/conversations/{mine['id']}")
    owner_list = client.get(f"/api/v1/accounts/{account['account']['id']}/ai/conversations").json()
    with acting_as(member):
        member_list = client.get(f"/api/v1/accounts/{account['account']['id']}/ai/conversations").json()

    assert owner_view.status_code == 404
    assert owner_list == [] and [c["id"] for c in member_list] == [mine["id"]]


def test_members_answers_only_use_what_they_can_see(client: TestClient, db_session: Session, account: dict, fake_model: FakeModel, answers: list) -> None:
    member = add_member(db_session, account, "alex@example.com", modules=["TASKS"])
    post(client, f"/api/v1/projects/{account['secret']['id']}/tasks", {"name": "Secret plan"})
    fake_model.tool_call("list_tasks", json.dumps({"project_id": account["secret"]["id"]}))
    fake_model.reply("I couldn't find that project.")

    with acting_as(member):
        conversation = start(client, account)
        ask(client, conversation, "What's in Secret?")

    tool_result = fake_model.calls[1]["body"]["messages"][-1]["content"]
    assert "Secret plan" not in tool_result and "doesn't have access" in tool_result
    assert "Alex" in fake_model.calls[0]["body"]["messages"][0]["content"]


def test_provider_errors_are_explained(client: TestClient, account: dict, fake_model: FakeModel, answers: list) -> None:
    conversation = start(client, account)
    fake_model.fail(401, "Incorrect API key provided: sk-test-1234567890abcd")

    answer = ask(client, conversation, "Hello?").json()["messages"][-1]

    assert answer["status"] == "FAILED"
    assert answer["error"].startswith("The AI provider rejected this account's key")
    assert "sk-test" not in json.dumps(answer)


def test_too_many_lookups_stops(client: TestClient, account: dict, fake_model: FakeModel, answers: list, ai_settings: Settings) -> None:
    ai_settings.ai_max_model_calls = 2
    conversation = start(client, account)
    fake_model.tool_call("list_projects", call_id="a")
    fake_model.tool_call("list_projects", call_id="b")

    answer = ask(client, conversation, "Loop forever").json()["messages"][-1]

    assert answer["status"] == "FAILED" and "more lookups" in answer["error"]
    assert answer["steps"] == ["Looking up projects"]  # the second request for tools isn't run


def test_sending_rules(client: TestClient, db_session: Session, account: dict, fake_model: FakeModel, ai_settings: Settings) -> None:
    app.dependency_overrides[get_ai_dispatcher] = lambda: (lambda run_id, background: None)  # leave runs queued
    conversation = start(client, account)

    first = ask(client, conversation, "One")
    busy = ask(client, conversation, "Two")
    ai_settings.ai_questions_per_hour = 1
    other = start(client, account)
    limited = ask(client, other, "Three")

    assert first.status_code == 202 and first.json()["messages"][-1]["status"] == "QUEUED"
    assert first.json()["active_run_id"] is not None
    assert busy.status_code == 409 and "Wait for the current answer" in busy.text
    assert limited.status_code == 429


def test_not_set_up_or_switched_off(client: TestClient, db_session: Session, fake_model: FakeModel, answers: list) -> None:
    bare = create_account(client)
    conversation = start(client, bare)

    not_set_up = ask(client, conversation, "Hi")
    client.patch(f"/api/v1/accounts/{bare['account']['id']}/ai/settings", json={"assistant_enabled": False})
    switched_off = ask(client, conversation, "Hi")

    assert not_set_up.status_code == 409 and "isn't set up" in not_set_up.text
    assert switched_off.status_code == 409 and "switched the assistant off" in switched_off.text
    assert answers == []


def test_stale_runs_are_failed(client: TestClient, db_session: Session, account: dict, ai_settings: Settings) -> None:
    app.dependency_overrides[get_ai_dispatcher] = lambda: (lambda run_id, background: None)
    conversation = start(client, account)
    ask(client, conversation, "Will the worker ever come?")
    run = db_session.scalars(select(AiRun)).one()
    run.created_at = datetime.now(UTC) - timedelta(minutes=10)
    db_session.commit()

    answer = client.get(f"/api/v1/ai/conversations/{conversation['id']}").json()["messages"][-1]

    assert answer["status"] == "FAILED" and answer["error"] == "That took too long to answer. Try again."


def test_a_run_is_only_answered_once(db_session: Session, client: TestClient, account: dict, fake_model: FakeModel, ai_settings: Settings, answers: list) -> None:
    conversation = start(client, account)
    fake_model.reply("Done.")
    ask(client, conversation, "Once")
    run = db_session.scalars(select(AiRun)).one()

    again = AssistantRunner(db_session, ai_settings, transport=fake_model, resolver=PUBLIC_RESOLVER).run(run.id)

    assert again == "DONE" and len(fake_model.calls) == 1


def test_retention_deletes_old_conversations(client: TestClient, db_session: Session, account: dict, ai_settings: Settings) -> None:
    old, recent = start(client, account), start(client, account)
    client.patch(f"/api/v1/accounts/{account['account']['id']}/ai/settings", json={"retention_days": 30})
    db_session.get(AiConversation, UUID(old["id"])).updated_at = datetime.now(UTC) - timedelta(days=40)
    db_session.get(AiConversation, UUID(recent["id"])).updated_at = datetime.now(UTC) - timedelta(days=10)
    db_session.commit()

    deleted = ConversationService(db_session, ai_settings).delete_expired()

    assert deleted == 1
    assert [c["id"] for c in client.get(f"/api/v1/accounts/{account['account']['id']}/ai/conversations").json()] == [recent["id"]]
    assert db_session.get(AiAccountSettings, UUID(account["account"]["id"])).retention_days == 30


def test_deleting_a_conversation(client: TestClient, account: dict) -> None:
    conversation = start(client, account)

    deleted = client.delete(f"/api/v1/ai/conversations/{conversation['id']}")

    assert deleted.status_code == 204
    assert client.get(f"/api/v1/ai/conversations/{conversation['id']}").status_code == 404
