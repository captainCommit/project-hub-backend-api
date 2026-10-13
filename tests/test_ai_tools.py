import json
from datetime import date, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.user import User
from app.services.ai import tools as tools_module
from app.services.ai.tools import TOOL_SPECS, ToolContext, run_tool, to_text
from tests.ai_support import add_member, client, create_account, db_session, dev_user, post  # noqa: F401
from tests.ai_support import ai_settings, fake_model  # noqa: F401


def call(db_session: Session, hierarchy: dict, user: User, name: str, **args: object) -> dict:
    ctx = ToolContext(db=db_session, account_id=UUID(hierarchy["account"]["id"]), user=user)
    return json.loads(run_tool(ctx, name, args))


def status_id(db_session: Session, hierarchy: dict, value: str) -> str:
    return str(
        db_session.scalar(
            select(OptionValue.id)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionSet.account_id == UUID(hierarchy["account"]["id"]),
                OptionSet.entity_type == "TASK",
                OptionSet.name == "STATUS",
                OptionValue.value == value,
            )
        )
    )


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> dict:
    hierarchy = create_account(client)
    alex = add_member(db_session, hierarchy, "alex@example.com", modules=["TASKS", "RISKS"])
    yesterday = str(date.today() - timedelta(days=1))
    late = post(client, f"/api/v1/projects/{hierarchy['apollo']['id']}/tasks", {"name": "Late report", "finish_date": yesterday})
    blocked = post(
        client,
        f"/api/v1/projects/{hierarchy['apollo']['id']}/tasks",
        {"name": "Vendor contract", "status_id": status_id(db_session, hierarchy, "BLOCKED")},
    )
    hidden = post(client, f"/api/v1/projects/{hierarchy['secret']['id']}/tasks", {"name": "Secret plan", "finish_date": yesterday})
    for task in (late, hidden):
        post(client, f"/api/v1/tasks/{task['id']}/assignments", {"user_id": str(alex.id)})
    risk = post(client, f"/api/v1/projects/{hierarchy['apollo']['id']}/risks", {"title": "Vendor may slip"})
    post(client, f"/api/v1/projects/{hierarchy['secret']['id']}/risks", {"title": "Secret risk"})
    post(client, f"/api/v1/entities/TASK/{late['id']}/comments", {"body": "Waiting on finance"})
    return {**hierarchy, "alex": alex, "late": late, "blocked": blocked, "hidden": hidden, "risk": risk}


def test_member_only_sees_projects_they_have_access_to(db_session: Session, world: dict) -> None:
    alex = world["alex"]

    projects = call(db_session, world, alex, "list_projects")
    secret_tasks = call(db_session, world, alex, "list_tasks", project_id=world["secret"]["id"])
    risks = call(db_session, world, alex, "list_raid_items", kind="risk")
    work = call(db_session, world, alex, "my_work")

    names = [p["name"] for f in projects["portfolios"] for g in f["programs"] for p in g["projects"]]
    assert names == ["Apollo"]
    assert secret_tasks == {"error": "Not found, or the person doesn't have access to it."}
    assert [r["title"] for r in risks["items"]] == ["Vendor may slip"]
    assert [t["name"] for t in work["items"]] == ["Late report"]
    assert work["items"][0]["link"] == f"/projects/{world['apollo']['id']}/sheet?task={world['late']['id']}"
    assert work["items"][0]["project"] == "Apollo"


def test_hidden_module_is_refused(db_session: Session, client: TestClient) -> None:
    hierarchy = create_account(client)
    viewer = add_member(db_session, hierarchy, "sam@example.com", modules=["TASKS"], view_modules=["TASKS"])
    post(client, f"/api/v1/projects/{hierarchy['apollo']['id']}/risks", {"title": "Hidden from Sam"})

    result = call(db_session, hierarchy, viewer, "list_raid_items", kind="risk", project_id=hierarchy["apollo"]["id"])

    assert "Hidden from Sam" not in json.dumps(result)


def test_status_filter_and_trimming(db_session: Session, world: dict) -> None:
    result = call(db_session, world, world["alex"], "list_tasks", project_id=world["apollo"]["id"], status="blocked")

    [task] = result["items"]
    assert task["name"] == "Vendor contract" and task["status"] == "Blocked"
    assert "status_id" not in task and "account_id" not in task  # internal fields are dropped


def test_search_stays_inside_the_account(client: TestClient, db_session: Session, world: dict) -> None:
    other = create_account(client, "Globex")
    post(client, f"/api/v1/projects/{other['apollo']['id']}/tasks", {"name": "Late report elsewhere"})
    owner = dev_user(db_session)

    result = call(db_session, world, owner, "search", query="Late report")

    assert [r["title"] for r in result["items"]] == ["Late report"]
    assert result["items"][0]["link"].endswith(f"task={world['late']['id']}")


def test_get_item_includes_comments_and_stays_in_account(client: TestClient, db_session: Session, world: dict) -> None:
    owner = dev_user(db_session)
    other = create_account(client, "Globex")
    elsewhere = post(client, f"/api/v1/projects/{other['apollo']['id']}/tasks", {"name": "Not this account"})

    item = call(db_session, world, owner, "get_item", kind="task", id=world["late"]["id"])
    hidden = call(db_session, world, world["alex"], "get_item", kind="task", id=world["hidden"]["id"])
    foreign = call(db_session, world, owner, "get_item", kind="task", id=elsewhere["id"])

    assert item["name"] == "Late report"
    assert item["recent_comments"][0]["text"] == "Waiting on finance"
    assert hidden["error"] == foreign["error"] == "Not found, or the person doesn't have access to it."


def test_bad_arguments_come_back_as_errors(db_session: Session, world: dict) -> None:
    alex = world["alex"]

    assert "kind must be one of" in call(db_session, world, alex, "list_raid_items", kind="vibes")["error"]
    assert "id returned by another tool" in call(db_session, world, alex, "project_overview", project_id="Apollo")["error"]
    assert call(db_session, world, alex, "make_coffee")["error"] == "There is no tool called make_coffee."


def test_overview_and_dashboard_run(db_session: Session, world: dict) -> None:
    alex = world["alex"]

    overview = call(db_session, world, alex, "project_overview", project_id=world["apollo"]["id"])
    dashboard = call(db_session, world, alex, "account_dashboard")

    assert "error" not in overview and overview["link"] == f"/projects/{world['apollo']['id']}/sheet"
    assert "error" not in dashboard
    assert "Secret" not in json.dumps(dashboard)


def test_large_results_are_shortened(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_module, "MAX_RESULT_CHARS", 500)
    text = to_text({"items": [{"name": "x" * 40}] * 50, "total": 50})

    result = json.loads(text)
    assert len(text) <= 500 and len(result["items"]) < 50 and "Showing" in result["note"]


def test_every_tool_has_an_object_schema() -> None:
    assert len({spec.name for spec in TOOL_SPECS}) == len(TOOL_SPECS) == 8
    for spec in TOOL_SPECS:
        assert spec.parameters["type"] == "object" and spec.description
