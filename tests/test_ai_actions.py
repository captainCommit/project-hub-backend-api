"""Compass doing work: drafting a risk, issue or task that the person reviews and creates; follow-ups; feedback."""

import json
from datetime import date, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import app
from app.models.risk import Risk
from app.models.task import Task
from app.routers.ai import get_ai_dispatcher
from app.services.ai.runner import AssistantRunner
from tests.ai_support import PUBLIC_RESOLVER, FakeModel, acting_as, add_member, client, create_account, db_session, post  # noqa: F401
from tests.ai_support import ai_settings, fake_model  # noqa: F401

KEY = {"provider": "OPENAI", "model": "gpt-test", "api_key": "sk-test-1234567890abcd", "accept_data_notice": True}


@pytest.fixture()
def answers(db_session: Session, ai_settings: Settings, fake_model: FakeModel, client: TestClient):
    def dispatch(run_id: UUID, background: object) -> None:
        AssistantRunner(db_session, ai_settings, transport=fake_model, resolver=PUBLIC_RESOLVER).run(run_id)

    app.dependency_overrides[get_ai_dispatcher] = lambda: dispatch


@pytest.fixture()
def account(client: TestClient, fake_model: FakeModel) -> dict:
    hierarchy = create_account(client)
    fake_model.tool_call("ping", '{"ok": true}')
    assert client.put(f"/api/v1/accounts/{hierarchy['account']['id']}/ai/key", json=KEY).status_code == 200
    fake_model.calls.clear()
    return hierarchy


def ask(client: TestClient, account: dict, text: str) -> dict:
    conversation = post(client, f"/api/v1/accounts/{account['account']['id']}/ai/conversations", {})
    response = client.post(f"/api/v1/ai/conversations/{conversation['id']}/messages", json={"content": text})
    assert response.status_code == 202, response.text
    return response.json()


RISK_ARGS = {
    "kind": "risk",
    "title": "Integration vendor delay",
    "cause": "Vendor integration delivery is delayed.",
    "effect": "Customer onboarding release may slip by two weeks.",
}


def test_a_drafted_risk_is_created_only_when_the_person_confirms(
    client: TestClient, db_session: Session, account: dict, fake_model: FakeModel, answers: None
) -> None:
    fake_model.tool_call("draft_record", json.dumps({**RISK_ARGS, "project_id": account["apollo"]["id"]}))
    fake_model.reply("I've drafted the risk below. Review it and press Create.\n\nFollow-ups: Who should own it? | Add a target date.")
    body = ask(client, account, "If our integration vendor is delayed, onboarding could slip two weeks. Add a risk to Apollo.")

    answer = body["messages"][-1]
    assert answer["content"] == "I've drafted the risk below. Review it and press Create."
    assert answer["follow_ups"] == ["Who should own it?", "Add a target date."]
    (action,) = answer["actions"]
    draft = action["draft"]
    assert (draft["project"], draft["program"], draft["priority_label"], draft["status_label"]) == ("Apollo", "Retail", "Medium", "Open")
    assert draft["cause"] == RISK_ARGS["cause"] and action["outcome"] is None
    assert db_session.scalars(select(Risk)).all() == [], "nothing is created by the model"
    tool_result = fake_model.calls[1]["body"]["messages"][-1]["content"]
    assert "NOT created" in tool_result

    created = client.post(f"/api/v1/ai/drafts/{action['id']}/create", json={"priority": "High"})
    assert created.status_code == 200, created.text
    outcome = created.json()
    risk = db_session.get(Risk, UUID(outcome["id"]))
    assert risk.title == "Integration vendor delay" and risk.cause == RISK_ARGS["cause"]
    assert outcome["number"] == risk.risk_number and outcome["link"] == f"/RiskLog?tab=risks&id={risk.id}"
    assert outcome["draft"]["priority_label"] == "High"

    again = client.post(f"/api/v1/ai/drafts/{action['id']}/create", json={})
    assert again.status_code == 409
    shown = client.get(f"/api/v1/ai/conversations/{body['id']}").json()["messages"][-1]["actions"][0]
    assert shown["outcome"]["status"] == "CREATED"


def test_a_draft_without_a_project_needs_one_before_it_is_created(
    client: TestClient, db_session: Session, account: dict, fake_model: FakeModel, answers: None
) -> None:
    due = str(date.today() + timedelta(days=7))
    fake_model.tool_call("draft_record", json.dumps({"kind": "task", "title": "Book the steering meeting", "target_date": due}))
    fake_model.reply("Which project should I add it to?")
    action = ask(client, account, "Add a task to book the steering meeting")["messages"][-1]["actions"][0]
    assert "project_id" not in action["draft"]

    refused = client.post(f"/api/v1/ai/drafts/{action['id']}/create", json={})
    assert refused.status_code == 400 and "project" in refused.text
    created = client.post(f"/api/v1/ai/drafts/{action['id']}/create", json={"project_id": account["apollo"]["id"]}).json()
    task = db_session.get(Task, UUID(created["id"]))
    assert task.name == "Book the steering meeting" and str(task.finish_date) == due
    assert created["link"] == f"/projects/{account['apollo']['id']}/sheet?task={task.id}"


def test_drafts_respect_the_persons_rights_and_belong_to_them(
    client: TestClient, db_session: Session, account: dict, fake_model: FakeModel, answers: None
) -> None:
    # Sam can see Apollo's tasks but not add risks there.
    sam = add_member(db_session, account, "sam@example.com", modules=["TASKS"], view_modules=["TASKS", "RISKS"])
    fake_model.tool_call("draft_record", json.dumps({**RISK_ARGS, "project_id": account["apollo"]["id"]}))
    fake_model.reply("You can't add risks there.")
    with acting_as(sam):
        ask(client, account, "Add a risk to Apollo")
    assert "can't add a risk" in fake_model.calls[1]["body"]["messages"][-1]["content"]

    # Someone else's draft can't be created by Sam.
    fake_model.tool_call("draft_record", json.dumps({**RISK_ARGS, "project_id": account["apollo"]["id"]}))
    fake_model.reply("Drafted.")
    action = ask(client, account, "Add a risk to Apollo")["messages"][-1]["actions"][0]
    with acting_as(sam):
        assert client.post(f"/api/v1/ai/drafts/{action['id']}/create", json={}).status_code == 404
    assert db_session.scalars(select(Risk)).all() == []


def test_thumbs_up_or_down_on_an_answer(client: TestClient, account: dict, fake_model: FakeModel, answers: None) -> None:
    fake_model.reply("All fine.")
    body = ask(client, account, "How are things?")
    answer_id = body["messages"][-1]["id"]
    assert body["messages"][-1]["follow_ups"] == []

    assert client.put(f"/api/v1/ai/answers/{answer_id}/feedback", json={"value": "up"}).status_code == 204
    assert client.get(f"/api/v1/ai/conversations/{body['id']}").json()["messages"][-1]["feedback"] == "up"
    assert client.put(f"/api/v1/ai/answers/{answer_id}/feedback", json={"value": None}).status_code == 204
    assert client.get(f"/api/v1/ai/conversations/{body['id']}").json()["messages"][-1]["feedback"] is None
