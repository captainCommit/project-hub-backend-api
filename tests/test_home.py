from datetime import date, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.change_request import ChangeRequest
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


def test_assumptions_and_dependencies_keep_assignee_and_target_date(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    due = date.today() + timedelta(days=4)

    assumption = create_raid_item(client, project_id, "assumptions", description="Vendor ready", assigned_to_name="Sam", target_date=due)
    dependency = create_raid_item(client, project_id, "dependencies", title="API contract", target_date=due)

    assert assumption["assigned_to_name"] == "Sam"
    assert assumption["target_date"] == str(due)
    assert dependency["target_date"] == str(due)
    response = client.patch(f"/api/v1/dependencies/{dependency['id']}", json={"target_date": None})
    assert response.status_code == 200
    assert response.json()["target_date"] is None


def test_home_includes_my_assumptions_and_dependencies(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    my_name = me(client)["full_name"]
    due = date.today() + timedelta(days=2)

    create_raid_item(client, project_id, "assumptions", description="Budget approved", assigned_to_name=my_name.upper(), target_date=due)
    create_raid_item(client, project_id, "dependencies", title="Data feed", assigned_to_name=my_name, target_date=due)
    create_raid_item(client, project_id, "dependencies", title="Not mine", assigned_to_name="Someone Else", target_date=due)

    home = get_home(client, account_id)

    kinds = {item["title"]: item["kind"] for item in home["priorities"]}
    assert kinds == {"Budget approved": "ASSUMPTION", "Data feed": "DEPENDENCY"}
    closed_id = str(get_option_id(db_session, account_id=account_id, entity_type="DEPENDENCY", option_name="STATUS", value="CLOSED"))
    assert next(item for item in home["priorities"] if item["kind"] == "DEPENDENCY")["complete_status_id"] == closed_id


def test_my_projects_are_the_ones_i_manage_or_have_tasks_on(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    program_id = hierarchy["program"]["id"]
    user = me(client)
    active_id = get_option_id(db_session, account_id=account_id, entity_type="PROJECT", option_name="STATUS", value="ACTIVE")
    on_hold_id = get_option_id(db_session, account_id=account_id, entity_type="PROJECT", option_name="STATUS", value="ON_HOLD")

    managed = create_project(client, program_id, "Managed", project_manager=user["full_name"], status_id=active_id)
    someone_elses = create_project(client, program_id, "Someone else's", project_manager="Pat Jones", status_id=active_id)
    worked_on = create_project(client, program_id, "Worked on", project_manager="Pat Jones", status_id=on_hold_id)
    assign(client, create_task(client, worked_on["id"], "Mine")["id"], user["id"])

    home = get_home(client, account_id)

    names = {project["name"] for project in home["my_projects"]}
    assert {"Managed", "Worked on"} <= names
    assert someone_elses["name"] not in names
    # Only projects whose status is Active count, not every unfinished one.
    assert home["active_project_count"] == len([p for p in home["my_projects"] if p["status"] and p["status"]["value"] == "ACTIVE"])
    assert managed["id"] in {project["id"] for project in home["my_projects"]}


def test_home_finds_my_risks_and_issues_outside_my_projects(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    user = me(client)
    due = date.today() + timedelta(days=3)
    elsewhere = create_project(client, hierarchy["program"]["id"], "Elsewhere", project_manager="Pat Jones")

    # Extra spaces and a different case still name me; a name that only contains mine doesn't.
    spaced_name = "  " + "   ".join(user["full_name"].upper().split()) + " "
    create_raid_item(client, elsewhere["id"], "risks", title="Named risk", assigned_to_name=spaced_name, target_resolution_date=due)
    create_raid_item(client, elsewhere["id"], "issues", title="Assigned issue", assigned_to=user["id"], target_resolution_date=due)
    create_raid_item(
        client, elsewhere["id"], "risks", title="Not mine", assigned_to_name=f"{user['full_name']} Jr", target_resolution_date=due
    )

    home = get_home(client, account_id)

    assert {item["title"]: item["kind"] for item in home["priorities"]} == {"Named risk": "RISK", "Assigned issue": "ISSUE"}
    assert elsewhere["id"] not in {project["id"] for project in home["my_projects"]}


def test_home_bell_lists_approvals_assignments_and_mentions(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    user = me(client)
    open_id = get_option_id(db_session, account_id=account_id, entity_type="CHANGE_REQUEST", option_name="STATUS", value="OPEN")
    draft_id = get_option_id(db_session, account_id=account_id, entity_type="CHANGE_REQUEST", option_name="STATUS", value="DRAFT")

    waiting = create_raid_item(client, project_id, "change-requests", title="Extend scope", status_id=open_id, proposed_by_name="Pat Jones")
    create_raid_item(client, project_id, "change-requests", title="Still a draft", status_id=draft_id, proposed_by_name="Pat Jones")
    # The local dev user created it, which counts as theirs, so it isn't waiting on them.
    db_session.get(ChangeRequest, UUID(waiting["id"])).created_by = None
    db_session.commit()
    task = create_task(client, project_id, "Write tests", start_date=date.today(), finish_date=date.today())
    assign(client, task["id"], user["id"])
    comment = client.post(f"/api/v1/entities/TASK/{task['id']}/comments", json={"body": f"Over to you @{user['full_name']}"})
    assert comment.status_code == 201

    home = get_home(client, account_id)

    kinds = sorted(item["kind"] for item in home["notifications"])
    assert kinds == ["APPROVAL", "ASSIGNMENT", "MENTION"]
    approval = next(item for item in home["notifications"] if item["kind"] == "APPROVAL")
    assert approval["entity_type"] == "CHANGE_REQUEST"
    assert approval["entity_id"] == waiting["id"]
    assert home["notifications_read_at"] is None

    marked = client.post(f"/api/v1/accounts/{account_id}/me/home/notifications/read")
    assert marked.status_code == 200
    assert get_home(client, account_id)["notifications_read_at"] is not None


def test_home_bell_items_can_be_removed(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    user = me(client)
    kept = create_task(client, hierarchy["project"]["id"], "Keep me", start_date=date.today(), finish_date=date.today())
    gone = create_task(client, hierarchy["project"]["id"], "Remove me", start_date=date.today(), finish_date=date.today())
    assign(client, kept["id"], user["id"])
    assign(client, gone["id"], user["id"])

    removed = client.post(
        f"/api/v1/accounts/{account_id}/me/home/notifications/dismiss",
        json={"ids": [f"assignment-{gone['id']}", "not-a-bell-item"]},
    )

    assert removed.status_code == 200
    assert removed.json() == {"removed": 1}
    ids = [item["id"] for item in get_home(client, account_id)["notifications"]]
    assert ids == [f"assignment-{kept['id']}"]
    # Removing it again changes nothing.
    again = client.post(f"/api/v1/accounts/{account_id}/me/home/notifications/dismiss", json={"ids": [f"assignment-{gone['id']}"]})
    assert again.json() == {"removed": 0}
