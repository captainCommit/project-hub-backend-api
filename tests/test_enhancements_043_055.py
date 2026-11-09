from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account_member import AccountMemberRole
from tests.test_account_dashboard import (  # noqa: F401 - fixtures
    client,
    create_project,
    create_task,
    create_work_hierarchy,
    db_session,
    set_current_user_role,
)


def my_projects(client: TestClient, account_id: str) -> list[dict]:
    response = client.get(f"/api/v1/accounts/{account_id}/me/home")
    assert response.status_code == 200
    return response.json()["my_projects"]


def test_a_project_can_be_added_to_my_projects_and_removed(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    other = create_project(client, hierarchy["program"]["id"], "Not assigned to me", project_manager="Pat Jones")
    assert other["id"] not in {project["id"] for project in my_projects(client, account_id)}

    pinned = client.put(f"/api/v1/accounts/{account_id}/me/home/projects/{other['id']}")
    assert pinned.status_code == 200
    assert client.put(f"/api/v1/accounts/{account_id}/me/home/projects/{other['id']}").status_code == 200

    listed = my_projects(client, account_id)
    assert listed[0]["id"] == other["id"]
    assert listed[0]["pinned"] is True

    assert client.delete(f"/api/v1/accounts/{account_id}/me/home/projects/{other['id']}").status_code == 200
    assert other["id"] not in {project["id"] for project in my_projects(client, account_id)}


def test_a_project_from_another_account_cannot_be_added(client: TestClient) -> None:
    first = create_work_hierarchy(client)
    response = client.put(f"/api/v1/accounts/{first['account']['id']}/me/home/projects/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 404


def test_dependency_numbers_use_the_short_prefix(client: TestClient) -> None:
    project_id = create_work_hierarchy(client)["project"]["id"]
    numbers = [
        client.post(f"/api/v1/projects/{project_id}/dependencies", json={"title": title}).json()["dependency_number"]
        for title in ("First", "Second")
    ]
    assert numbers == ["D-001", "D-002"]


def test_decision_option_effort_is_a_quantity_with_resources(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    decision = client.post(f"/api/v1/projects/{hierarchy['project']['id']}/decisions", json={"title": "Pick one"}).json()

    created = client.post(
        f"/api/v1/decisions/{decision['id']}/options",
        json={"title": "Build", "work_effort_value": "120", "work_effort_unit": "HOURS", "implementation_resources": ["Portal Team"]},
    )
    assert created.status_code == 201, created.json()
    option = created.json()
    assert float(option["work_effort_value"]) == 120
    assert option["work_effort_unit"] == "HOURS"
    assert option["implementation_resources"] == ["Portal Team"]

    updated = client.patch(f"/api/v1/decision-options/{option['id']}", json={"work_effort_value": "2", "work_effort_unit": "WEEKS"})
    assert updated.status_code == 200
    assert updated.json()["work_effort_unit"] == "WEEKS"
    assert client.patch(f"/api/v1/decision-options/{option['id']}", json={"work_effort_unit": "MONTHS"}).status_code == 422

    listed = client.get(f"/api/v1/accounts/{hierarchy['account']['id']}/decisions").json()
    row = next(item for item in (listed["items"] if isinstance(listed, dict) else listed) if item["id"] == decision["id"])
    assert float(row["work_effort_value"]) == 2
    assert row["work_effort_unit"] == "WEEKS"


def test_change_request_has_implementation_fields(client: TestClient) -> None:
    project_id = create_work_hierarchy(client)["project"]["id"]
    created = client.post(f"/api/v1/projects/{project_id}/change-requests", json={"title": "Add SSO"})
    assert created.status_code == 201
    assert created.json()["implementation_resources"] == []

    updated = client.patch(
        f"/api/v1/change-requests/{created.json()['id']}",
        json={"implementation_resources": ["Platform Engineering", "Billing Operations"], "completion_date": "2026-11-06"},
    )
    assert updated.status_code == 200, updated.json()
    assert updated.json()["implementation_resources"] == ["Platform Engineering", "Billing Operations"]
    assert updated.json()["completion_date"] == "2026-11-06"


def test_a_story_links_to_a_requirement_in_its_own_project(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    task = create_task(client, project_id, "Story")
    requirement = client.post(f"/api/v1/projects/{project_id}/requirements", json={"title": "Users can export"}).json()

    linked = client.patch(f"/api/v1/tasks/{task['id']}", json={"requirement_id": requirement["id"]})
    assert linked.status_code == 200, linked.json()
    assert linked.json()["requirement_id"] == requirement["id"]
    listed = client.get(f"/api/v1/accounts/{hierarchy['account']['id']}/tasks", params={"project_id": project_id}).json()
    assert next(item for item in listed if item["id"] == task["id"])["requirement_id"] == requirement["id"]

    other_project = create_project(client, hierarchy["program"]["id"], "Other")
    elsewhere = client.post(f"/api/v1/projects/{other_project['id']}/requirements", json={"title": "Elsewhere"}).json()
    assert client.patch(f"/api/v1/tasks/{task['id']}", json={"requirement_id": elsewhere["id"]}).status_code == 400

    cleared = client.patch(f"/api/v1/tasks/{task['id']}", json={"requirement_id": None})
    assert cleared.json()["requirement_id"] is None


def test_weekly_allocation_override_round_trip(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    resource = client.post(f"/api/v1/accounts/{account_id}/resources", json={"name": "Aisha"}).json()
    url = f"/api/v1/resources/{resource['id']}/project-allocations/{project_id}"
    overrides = lambda: client.get(f"/api/v1/accounts/{account_id}/resource-project-allocations").json()  # noqa: E731

    assert client.put(url, json={"weekly_hours": "2.4"}).status_code == 200
    assert client.put(url, json={"weekly_hours": "3.2"}).status_code == 200
    assert [float(item["weekly_hours"]) for item in overrides()] == [3.2]

    assert client.put(url, json={"weekly_hours": None}).status_code == 200
    assert overrides() == []

    assert client.put(url, json={"weekly_hours": "1"}).status_code == 200
    assert client.delete(f"/api/v1/resources/{resource['id']}/project-allocations").status_code == 204
    assert overrides() == []

    assert client.put(url, json={"weekly_hours": "-1"}).status_code == 422
    set_current_user_role(db_session, account_id, AccountMemberRole.VIEWER)
    assert client.put(url, json={"weekly_hours": "1"}).status_code == 403
