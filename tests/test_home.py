from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_account_dashboard import (  # noqa: F401 - fixtures
    client,
    create_project,
    create_raid_item,
    create_task,
    create_work_hierarchy,
    db_session,
    get_option_id,
)


def me(client: TestClient) -> dict:
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    return response.json()


def assign(client: TestClient, task_id: str, user_id: str) -> None:
    response = client.post(f"/api/v1/tasks/{task_id}/assignments", json={"user_id": user_id})
    assert response.status_code == 201


def get_home(client: TestClient, account_id: str) -> dict:
    response = client.get(f"/api/v1/accounts/{account_id}/me/home")
    assert response.status_code == 200
    return response.json()


def test_home_lists_my_dated_work_by_bucket(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    user_id = me(client)["id"]
    today = date.today()

    overdue = create_task(client, project_id, "Overdue", start_date=today - timedelta(days=5), finish_date=today - timedelta(days=2))
    soon = create_task(client, project_id, "Soon", start_date=today, finish_date=today + timedelta(days=3))
    later = create_task(client, project_id, "Later", start_date=today, finish_date=today + timedelta(days=20))
    not_mine = create_task(client, project_id, "Someone else's", start_date=today, finish_date=today + timedelta(days=1))
    for task in (overdue, soon, later):
        assign(client, task["id"], user_id)
    assign_other = client.post(f"/api/v1/tasks/{not_mine['id']}/assignments", json={"resource_name": "Vendor"})
    assert assign_other.status_code == 201
    create_raid_item(
        client, project_id, "risks", title="Vendor slips", assigned_to=user_id, target_resolution_date=today + timedelta(days=1)
    )
    create_raid_item(client, project_id, "risks", title="Unassigned risk", target_resolution_date=today)

    home = get_home(client, account_id)

    titles = [item["title"] for item in home["priorities"]]
    assert titles == ["Overdue", "Vendor slips", "Soon", "Later"]
    assert [item["bucket"] for item in home["priorities"]] == ["OVERDUE", "WEEK", "WEEK", "UPCOMING"]
    assert home["counts"] == {"all": 4, "overdue": 1, "week": 2, "upcoming": 1}
    complete_id = str(get_option_id(db_session, account_id=account_id, entity_type="TASK", option_name="STATUS", value="COMPLETE"))
    closed_id = str(get_option_id(db_session, account_id=account_id, entity_type="RISK", option_name="STATUS", value="CLOSED"))
    assert home["priorities"][0]["complete_status_id"] == complete_id
    assert home["priorities"][1]["kind"] == "RISK"
    assert home["priorities"][1]["complete_status_id"] == closed_id
    assert home["priorities"][0]["project"] == {"id": project_id, "name": "Project"}


def test_home_skips_completed_work(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    user_id = me(client)["id"]
    today = date.today()
    complete_id = get_option_id(db_session, account_id=account_id, entity_type="TASK", option_name="STATUS", value="COMPLETE")

    task = create_task(client, project_id, "Done already", start_date=today, finish_date=today, status_id=complete_id)
    assign(client, task["id"], user_id)

    assert get_home(client, account_id)["priorities"] == []


def test_home_summarises_my_projects(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    today = date.today()
    complete_id = get_option_id(db_session, account_id=account_id, entity_type="TASK", option_name="STATUS", value="COMPLETE")
    milestone_id = get_option_id(db_session, account_id=account_id, entity_type="TASK", option_name="TYPE", value="MILESTONE")

    create_task(client, project_id, "Finished", start_date=today, finish_date=today, status_id=complete_id)
    create_task(client, project_id, "Open", start_date=today, finish_date=today + timedelta(days=2))
    create_task(client, project_id, "Go-live", start_date=today + timedelta(days=9), finish_date=today + timedelta(days=9), task_type_id=milestone_id)
    create_task(client, project_id, "Kickoff", start_date=today - timedelta(days=9), finish_date=today - timedelta(days=9), task_type_id=milestone_id, status_id=complete_id)

    home = get_home(client, account_id)

    # The project was created by the signed-in user, so it counts as theirs.
    assert home["active_project_count"] == 1
    [project] = home["my_projects"]
    assert project["id"] == project_id
    assert project["percent_complete"] == 50
    assert project["is_completed"] is False
    assert project["next_milestone"]["name"] == "Go-live"
    assert any(entry["project"] and entry["project"]["id"] == project_id for entry in home["recent_activity"])
    assert all(entry["entity_name"] for entry in home["recent_activity"] if entry["entity_type"] == "TASK")


def test_non_member_cannot_read_home(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    from uuid import UUID

    from sqlalchemy import delete

    from app.models.account_member import AccountMember

    db_session.execute(delete(AccountMember).where(AccountMember.account_id == UUID(account_id)))
    db_session.commit()

    response = client.get(f"/api/v1/accounts/{account_id}/me/home")
    assert response.status_code == 403


def test_project_manager_can_be_set_and_cleared(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    program_id = hierarchy["program"]["id"]

    project = create_project(client, program_id, "Managed", project_manager="Jane Doe")
    assert project["project_manager"] == "Jane Doe"

    response = client.patch(f"/api/v1/projects/{project['id']}", json={"project_manager": "Sam Lee"})
    assert response.status_code == 200
    assert response.json()["project_manager"] == "Sam Lee"

    response = client.patch(f"/api/v1/projects/{project['id']}", json={"project_manager": None})
    assert response.json()["project_manager"] is None
