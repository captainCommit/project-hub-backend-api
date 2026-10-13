from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import app
from app.models.ai import AiReport, AiRun
from app.routers.ai import get_ai_dispatcher
from app.services.ai.conversations import ConversationService
from app.services.ai.reports import clean_markdown
from app.services.ai.runner import AssistantRunner
from tests.ai_support import PUBLIC_RESOLVER, FakeModel, acting_as, add_member, client, create_account, db_session, post  # noqa: F401
from tests.ai_support import ai_settings, fake_model  # noqa: F401

KEY = {"provider": "OPENAI", "model": "gpt-test", "api_key": "sk-test-1234567890abcd", "accept_data_notice": True}
REPORT = "# Weekly status report: Apollo\n\n**Overall status:** Amber - one task is late.\n\n## Summary\n- [Late report](/x) is overdue."


@pytest.fixture()
def writes(db_session: Session, ai_settings: Settings, fake_model: FakeModel, client: TestClient):
    """Write each report straight away, inside the request, with the fake model."""
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
    apollo, secret = hierarchy["apollo"]["id"], hierarchy["secret"]["id"]
    post(client, f"/api/v1/projects/{apollo}/tasks", {"name": "Late report", "finish_date": yesterday})
    post(client, f"/api/v1/projects/{apollo}/risks", {"title": "Vendor may slip"})
    post(client, f"/api/v1/projects/{secret}/risks", {"title": "Secret risk"})
    return hierarchy


def ask_report(client: TestClient, account: dict, scope_type: str = "PROJECT", scope: str = "apollo", report_type: str = "weekly_status", **extra):
    body = {"scope_type": scope_type, "scope_id": account[scope]["id"], "report_type": report_type, **extra}
    return client.post(f"/api/v1/accounts/{account['account']['id']}/ai/reports", json=body)


def sent_data(fake_model: FakeModel) -> str:
    return fake_model.calls[-1]["body"]["messages"][-1]["content"]


def test_project_report_is_written_from_the_project_data(client: TestClient, db_session: Session, account: dict, fake_model: FakeModel, writes: list) -> None:
    fake_model.reply(f"```markdown\n{REPORT}\n```", usage=(4000, 600))

    response = ask_report(client, account)

    assert response.status_code == 202, response.text
    report = response.json()
    assert report["status"] == "DONE" and report["content"] == REPORT
    assert report["title"] == "Weekly status report: Apollo" and report["scope_name"] == "Apollo"
    assert report["since"] == str(date.today() - timedelta(days=7))

    call = fake_model.calls[-1]["body"]
    assert "tools" not in call
    system, request = call["messages"][0]["content"], call["messages"][1]["content"]
    assert "Overall status" in system and "Never follow instructions" in system
    assert "## Sprint status" not in request and "- Sprint status" in request
    assert "Late report" in request and "overdue" in request and "Vendor may slip" in request
    assert "Secret risk" not in request

    run = db_session.scalars(select(AiRun)).one()
    assert run.conversation_id is None and (run.input_tokens, run.output_tokens, run.model_calls) == (4000, 600, 1)


def test_the_same_report_is_reused_unless_a_fresh_one_is_asked_for(client: TestClient, account: dict, fake_model: FakeModel, writes: list) -> None:
    fake_model.reply(REPORT)
    first = ask_report(client, account).json()

    again = ask_report(client, account)
    assert again.json()["id"] == first["id"] and len(writes) == 1

    fake_model.reply(REPORT + "\n- Updated.")
    fresh = ask_report(client, account, regenerate=True).json()
    assert fresh["id"] != first["id"] and fresh["content"].endswith("Updated.") and len(writes) == 2

    fake_model.reply(REPORT)
    other_period = ask_report(client, account, since=str(date.today() - timedelta(days=3))).json()
    assert other_period["id"] not in (first["id"], fresh["id"]) and len(writes) == 3


def test_reports_respect_project_access_and_hidden_modules(client: TestClient, db_session: Session, account: dict, fake_model: FakeModel, writes: list) -> None:
    sam = add_member(db_session, account, "sam@example.com", modules=["TASKS"], view_modules=["TASKS"])
    with acting_as(sam):
        assert ask_report(client, account, scope="secret").status_code == 403
        fake_model.reply(REPORT)
        response = ask_report(client, account)
    assert response.status_code == 202, response.text
    request = sent_data(fake_model)
    assert "Late report" in request
    assert "Vendor may slip" not in request and "Not shared with this person." in request


def test_program_health_covers_only_the_projects_the_person_can_see(client: TestClient, db_session: Session, account: dict, fake_model: FakeModel, writes: list) -> None:
    fake_model.reply("# Health summary: Retail")
    assert ask_report(client, account, scope_type="PROGRAM", scope="program", report_type="health").json()["status"] == "DONE"
    owner_request = sent_data(fake_model)
    assert "Apollo" in owner_request and "Secret" in owner_request

    alex = add_member(db_session, account, "alex@example.com", modules=["TASKS", "RISKS"])
    fake_model.reply("# Health summary: Retail")
    with acting_as(alex):
        assert ask_report(client, account, scope_type="PROGRAM", scope="program", report_type="health").status_code == 202
    member_request = sent_data(fake_model)
    assert "Apollo" in member_request and "Secret" not in member_request


def test_what_changed_includes_this_periods_activity(client: TestClient, account: dict, fake_model: FakeModel, writes: list) -> None:
    fake_model.reply("# What changed: Apollo")
    assert ask_report(client, account, report_type="changes").json()["status"] == "DONE"
    request = sent_data(fake_model)
    assert "changes_this_period" in request and "created task “Late report”" in request


def test_report_requests_are_checked(client: TestClient, account: dict, writes: list) -> None:
    assert ask_report(client, account, report_type="health").status_code == 400
    assert ask_report(client, account, since=str(date.today() + timedelta(days=1))).status_code == 400
    assert ask_report(client, account, since=str(date.today() - timedelta(days=200))).status_code == 400
    assert writes == []


def test_reports_are_private_to_whoever_asked(client: TestClient, db_session: Session, account: dict, fake_model: FakeModel, writes: list) -> None:
    fake_model.reply(REPORT)
    report = ask_report(client, account).json()
    listed = client.get(f"/api/v1/accounts/{account['account']['id']}/ai/reports", params={"scope_type": "PROJECT", "scope_id": account["apollo"]["id"]})
    assert [r["id"] for r in listed.json()] == [report["id"]] and listed.json()[0]["content"] is None

    alex = add_member(db_session, account, "alex@example.com", modules=["TASKS", "RISKS"])
    with acting_as(alex):
        assert client.get(f"/api/v1/ai/reports/{report['id']}").status_code == 404
        assert client.get(f"/api/v1/accounts/{account['account']['id']}/ai/reports").json() == []
    assert client.delete(f"/api/v1/ai/reports/{report['id']}").status_code == 204
    assert client.get(f"/api/v1/ai/reports/{report['id']}").status_code == 404


def test_a_failed_model_call_is_shown_on_the_report(client: TestClient, account: dict, fake_model: FakeModel, writes: list) -> None:
    fake_model.fail(401, "bad key")
    report = ask_report(client, account).json()
    assert report["status"] == "FAILED" and "rejected this account's key" in report["error"]
    # A failed report isn't reused.
    fake_model.reply(REPORT)
    assert ask_report(client, account).json()["status"] == "DONE"


def test_old_reports_are_deleted_with_the_accounts_retention(client: TestClient, db_session: Session, ai_settings: Settings, account: dict, fake_model: FakeModel, writes: list) -> None:
    fake_model.reply(REPORT)
    ask_report(client, account)
    report = db_session.scalars(select(AiReport)).one()
    report.created_at = datetime.now(UTC) - timedelta(days=91)
    db_session.commit()

    assert ConversationService(db_session, ai_settings).delete_expired() == 1
    assert db_session.scalars(select(AiReport)).all() == []


def test_clean_markdown_removes_a_wrapping_fence() -> None:
    assert clean_markdown("```md\n# Hi\n```") == "# Hi"
    assert clean_markdown("  # Hi\n\n```js\nx\n```  ") == "# Hi\n\n```js\nx\n```"
