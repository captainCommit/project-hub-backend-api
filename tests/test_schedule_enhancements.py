"""Assumption priority, task related records and comment counts, nudges, and support-ticket phone details."""

from uuid import uuid4

from fastapi.testclient import TestClient

from tests.test_tasks import client, create_task, create_work_hierarchy, db_session  # noqa: F401  (fixtures)


def test_assumptions_take_a_priority(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/assumptions",
        json={"description": "Vendor delivers on time", "priority": "high"},
    )
    assert response.status_code == 201, response.text
    assumption = response.json()
    assert assumption["priority"]["value"] == "HIGH"

    updated = client.patch(f"/api/v1/assumptions/{assumption['id']}", json={"priority": "critical"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["priority"]["value"] == "CRITICAL"

    listed = client.get(f"/api/v1/accounts/{hierarchy['account']['id']}/assumptions").json()
    rows = listed["items"] if isinstance(listed, dict) else listed
    assert rows[0]["priority"]["value"] == "CRITICAL"


def test_task_related_records_are_saved_once_and_returned(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Build")
    risk_id, decision_id = str(uuid4()), str(uuid4())

    response = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"related_records": [
            {"type": "RISK", "id": risk_id},
            {"type": "DECISION", "id": decision_id},
            {"type": "RISK", "id": risk_id},
        ]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["related_records"] == [{"type": "RISK", "id": risk_id}, {"type": "DECISION", "id": decision_id}]

    tree = client.get(f"/api/v1/projects/{hierarchy['project']['id']}/tasks/tree").json()
    assert tree[0]["related_records"][0]["id"] == risk_id
    gantt = client.get(f"/api/v1/projects/{hierarchy['project']['id']}/gantt").json()
    assert gantt["tasks"][0]["related_records"][1]["type"] == "DECISION"

    cleared = client.patch(f"/api/v1/tasks/{task['id']}", json={"related_records": []})
    assert cleared.json()["related_records"] == []


def test_task_related_records_reject_unknown_types(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    response = client.patch(f"/api/v1/tasks/{task['id']}", json={"related_records": [{"type": "SPRINT", "id": str(uuid4())}]})
    assert response.status_code == 422


def test_task_reads_count_comments(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    for body in ("First", "Second"):
        assert client.post(f"/api/v1/entities/TASK/{task['id']}/comments", json={"body": body}).status_code == 201

    tree = client.get(f"/api/v1/projects/{hierarchy['project']['id']}/tasks/tree").json()
    assert tree[0]["comment_count"] == 2


def test_nudge_needs_someone_assigned(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    response = client.post(f"/api/v1/tasks/{task['id']}/nudge", json={})
    assert response.status_code == 400
    assert "Nobody is assigned" in response.text


def test_nudge_explains_who_could_not_be_reached(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    assert client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"resource_name": "External Vendor"}).status_code == 201

    response = client.post(f"/api/v1/tasks/{task['id']}/nudge", json={"note": "Due Friday"})

    assert response.status_code == 400
    assert "External Vendor" in response.text and "No Project Hub login" in response.text


def test_support_ticket_keeps_phone_preference(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    response = client.post(
        f"/api/v1/accounts/{hierarchy['account']['id']}/support-tickets",
        json={"description": "Cannot export", "phone_call_ok": True, "phone_number": "+1 555 000 0000"},
    )
    assert response.status_code == 201, response.text
    ticket = response.json()
    assert ticket["phone_call_ok"] is True
    assert ticket["phone_number"] == "+1 555 000 0000"

    plain = client.post(f"/api/v1/accounts/{hierarchy['account']['id']}/support-tickets", json={"description": "Question"}).json()
    assert plain["phone_call_ok"] is False and plain["phone_number"] is None
