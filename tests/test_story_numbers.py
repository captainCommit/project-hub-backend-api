from fastapi.testclient import TestClient

from tests.test_hierarchy import client, create_account, create_portfolio, create_program, create_project, db_session  # noqa: F401


def test_tasks_get_story_numbers_per_project_from_100(client: TestClient) -> None:
    account = create_account(client)
    program = create_program(client, create_portfolio(client, account["id"])["id"])
    project_a = create_project(client, program["id"], "Project A")
    project_b = create_project(client, program["id"], "Project B")

    first = client.post(f"/api/v1/projects/{project_a['id']}/tasks", json={"name": "Login"}).json()
    second = client.post(f"/api/v1/projects/{project_a['id']}/tasks", json={"name": "Logout"}).json()
    other = client.post(f"/api/v1/projects/{project_b['id']}/tasks", json={"name": "Search"}).json()

    assert (first["story_number"], second["story_number"], other["story_number"]) == (100, 101, 100)


def test_sprint_capacity_is_saved_and_updated(client: TestClient) -> None:
    account = create_account(client)
    program = create_program(client, create_portfolio(client, account["id"])["id"])
    project = create_project(client, program["id"])

    sprint = client.post(f"/api/v1/projects/{project['id']}/sprints", json={"name": "Sprint 1", "capacity": 40}).json()
    updated = client.patch(f"/api/v1/sprints/{sprint['id']}", json={"capacity": 34}).json()

    assert sprint["capacity"] == 40
    assert updated["capacity"] == 34
    assert client.post(f"/api/v1/projects/{project['id']}/sprints", json={"name": "Sprint 2", "capacity": -1}).status_code == 422
