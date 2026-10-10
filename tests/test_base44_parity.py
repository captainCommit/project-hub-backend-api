"""Endpoints added to replace the Base44 entities the UI still depended on."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.account_member import AccountMemberRole
from tests.test_raid import client, create_work_hierarchy, db_session, set_current_user_role  # noqa: F401


PROJECT_LOG_CASES = (
    ("dependencies", "dependency_number", "DEP", {"title": "Vendor API", "impact_of_delay": "Launch slips"}),
    ("change-requests", "change_request_number", "CR", {"title": "Add SSO", "related_record": "DEC-001"}),
    ("requirements", "requirement_number", "REQ", {"title": "Users can export"}),
    ("test-cases", "test_case_number", "TC", {"title": "Export works", "result": "pass"}),
)


def create_resource(client: TestClient, account_id: str, name: str = "Jane Doe") -> dict:
    response = client.post(
        f"/api/v1/accounts/{account_id}/resources",
        json={"name": name, "department": "PMO", "hourly_rate": "120", "non_project_hours": "4"},
    )
    assert response.status_code == 201, response.json()
    return response.json()


@pytest.mark.parametrize("case", PROJECT_LOG_CASES, ids=[case[0] for case in PROJECT_LOG_CASES])
def test_project_log_crud_round_trip(client: TestClient, case: tuple) -> None:
    collection, number_field, prefix, payload = case
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    account_id = hierarchy["account"]["id"]

    created = client.post(f"/api/v1/projects/{project_id}/{collection}", json={**payload, "priority": "high"})
    assert created.status_code == 201, created.json()
    item = created.json()
    assert item[number_field] == f"{prefix}-001"
    assert item["priority"]["value"] == "HIGH"
    assert item["project"]["id"] == project_id
    assert item["portfolio_id"] == hierarchy["portfolio"]["id"]

    listed = client.get(f"/api/v1/accounts/{account_id}/{collection}")
    assert [row["id"] for row in listed.json()] == [item["id"]]
    assert client.get(f"/api/v1/projects/{project_id}/{collection}").json()[0]["id"] == item["id"]

    patched = client.patch(f"/api/v1/{collection}/{item['id']}", json={"title": "Renamed"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"

    assert client.delete(f"/api/v1/{collection}/{item['id']}").status_code == 204
    assert client.get(f"/api/v1/{collection}/{item['id']}").status_code == 404


@pytest.mark.parametrize(
    ("collection", "status_key", "expected_value"),
    (
        ("dependencies", "escalated", "ESCALATED"),
        ("change-requests", "in_review", "IN_REVIEW"),
        ("requirements", "validated", "VALIDATED"),
        ("risks", "escalated", "ESCALATED"),
        ("issues", "blocked", "BLOCKED"),
        ("decisions", "deferred", "DEFERRED"),
        ("assumptions", "in_review", "IN_REVIEW"),
    ),
)
def test_status_value_keys_resolve_to_options(
    client: TestClient,
    collection: str,
    status_key: str,
    expected_value: str,
) -> None:
    hierarchy = create_work_hierarchy(client)
    payload = {"description": "Text"} if collection == "assumptions" else {"title": "Item"}

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/{collection}",
        json={**payload, "status": status_key},
    )

    assert response.status_code == 201, response.json()
    assert response.json()["status"]["value"] == expected_value


def test_unknown_status_key_is_rejected(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/issues",
        json={"title": "Item", "status": "not-a-status"},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Invalid issue status."


def test_default_status_applied_for_new_log_types(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    response = client.post(f"/api/v1/projects/{hierarchy['project']['id']}/change-requests", json={"title": "CR"})

    assert response.json()["status"]["value"] == "DRAFT"


def test_risk_person_names_and_author_are_returned(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    created = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/risks",
        json={"title": "Risk", "assigned_to_name": "Jane Doe"},
    )

    assert created.status_code == 201
    assert created.json()["assigned_to_name"] == "Jane Doe"
    assert created.json()["created_by_name"] == "Dev User"


def test_decision_priority_and_links(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    created = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/decisions",
        json={"title": "Go", "priority": "critical", "proposed_by_name": "Ann", "related_record": "CR-001"},
    )

    assert created.status_code == 201, created.json()
    body = created.json()
    assert body["priority"]["value"] == "CRITICAL"
    assert (body["proposed_by_name"], body["related_record"]) == ("Ann", "CR-001")


def test_test_case_links_to_requirement_in_same_project(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    other = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    requirement = client.post(f"/api/v1/projects/{project_id}/requirements", json={"title": "Req"}).json()
    foreign_requirement = client.post(
        f"/api/v1/projects/{other['project']['id']}/requirements",
        json={"title": "Other req"},
    ).json()

    linked = client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={"title": "TC", "requirement_id": requirement["id"]},
    )
    cross_project = client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={"title": "TC", "requirement_id": foreign_requirement["id"]},
    )

    assert linked.status_code == 201
    assert linked.json()["result"] == "NOT_EXECUTED"
    assert cross_project.status_code == 400
    refreshed = client.get(f"/api/v1/requirements/{requirement['id']}").json()
    assert refreshed["test_case_ids"] == [linked.json()["id"]]
    assert refreshed["test_case_numbers"] == [linked.json()["test_case_number"]]
    assert linked.json()["requirement_number"] == requirement["requirement_number"]


def test_viewer_cannot_create_project_log(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.post(f"/api/v1/projects/{hierarchy['project']['id']}/dependencies", json={"title": "Dep"})

    assert response.status_code == 403


def test_resource_cost_fields_round_trip(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    resource = create_resource(client, hierarchy["account"]["id"])

    patched = client.patch(f"/api/v1/resources/{resource['id']}", json={"hourly_rate": "150.5"})

    assert resource["department"] == "PMO"
    assert resource["non_project_hours"] == "4.00"
    assert patched.json()["hourly_rate"] == "150.50"


def test_project_budget_round_trip(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]

    patched = client.patch(f"/api/v1/projects/{project_id}", json={"budget": "25000"})

    assert patched.status_code == 200
    assert patched.json()["budget"] == "25000.00"


def test_update_me_profile(client: TestClient) -> None:
    response = client.patch("/api/v1/me", json={"first_name": "Ada", "last_name": "Lovelace", "title": "PM"})

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Ada Lovelace"
    assert body["title"] == "PM"
    assert client.get("/api/v1/me").json()["first_name"] == "Ada"


def test_shared_access_lifecycle(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    payload = {"item_type": "project", "item_id": project_id, "shared_with_email": "Guest@Example.com"}

    created = client.post("/api/v1/shared-access", json=payload)
    duplicate = client.post("/api/v1/shared-access", json=payload)
    share_id = created.json()["id"]
    updated = client.patch(f"/api/v1/shared-access/{share_id}", json={"access_level": "edit"})
    listed = client.get("/api/v1/shared-access", params={"item_type": "PROJECT", "item_id": project_id})

    assert created.status_code == 201
    assert created.json()["shared_with_email"] == "guest@example.com"
    assert duplicate.status_code == 409
    assert updated.json()["access_level"] == "EDIT"
    assert [row["id"] for row in listed.json()] == [share_id]
    assert client.delete(f"/api/v1/shared-access/{share_id}").status_code == 204


def test_templates_lifecycle(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]

    created = client.post(
        f"/api/v1/accounts/{account_id}/templates",
        json={"name": "Agile", "content": {"columns": ["title", "status"]}},
    )
    template_id = created.json()["id"]
    updated = client.patch(f"/api/v1/templates/{template_id}", json={"description": "Two columns"})
    listed = client.get(f"/api/v1/accounts/{account_id}/templates", params={"template_type": "project"})

    assert created.status_code == 201
    assert created.json()["template_type"] == "PROJECT"
    assert updated.json()["content"] == {"columns": ["title", "status"]}
    assert [row["id"] for row in listed.json()] == [template_id]
    assert client.delete(f"/api/v1/templates/{template_id}").status_code == 204


def test_support_ticket_conversation(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]

    created = client.post(
        f"/api/v1/accounts/{account_id}/support-tickets",
        json={"category": "bug", "subject": "Broken export", "description": "Export fails"},
    )
    ticket = created.json()
    replied = client.post(f"/api/v1/support-tickets/{ticket['id']}/messages", json={"content": "Any update?"})
    resolved = client.patch(f"/api/v1/support-tickets/{ticket['id']}", json={"status": "resolved"})

    assert created.status_code == 201, created.json()
    assert ticket["ticket_number"] == "TKT-0001"
    assert ticket["category"] == "BUG"
    assert ticket["messages"][0]["content"] == "Export fails"
    assert [message["content"] for message in replied.json()["messages"]] == ["Export fails", "Any update?"]
    assert resolved.json()["status"] == "RESOLVED"
    assert [row["id"] for row in client.get(f"/api/v1/accounts/{account_id}/support-tickets").json()] == [ticket["id"]]


def test_comments_and_activity_support_new_entities(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    dependency = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/dependencies",
        json={"title": "Dep"},
    ).json()

    comment = client.post(f"/api/v1/entities/DEPENDENCY/{dependency['id']}/comments", json={"body": "Chasing vendor"})
    activity = client.get(f"/api/v1/entities/DEPENDENCY/{dependency['id']}/activity")

    assert comment.status_code == 201, comment.json()
    assert comment.json()["created_by_name"] == "Dev User"
    assert activity.status_code == 200
    listed = client.get(f"/api/v1/projects/{hierarchy['project']['id']}/dependencies").json()
    assert listed[0]["comment_count"] == 1


def create_task(client: TestClient, project_id: str, name: str = "Build") -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/tasks", json={"name": name})
    assert response.status_code == 201, response.json()
    return response.json()


def test_task_assignment_hours_and_listing(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])

    created = client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"resource_name": "Jane", "hours": "6"})
    assignment_id = created.json()["id"]
    updated = client.patch(f"/api/v1/task-assignments/{assignment_id}", json={"hours": "7.5"})
    listed = client.get(f"/api/v1/tasks/{task['id']}/assignments")

    assert created.status_code == 201
    assert updated.json()["hours"] == "7.50"
    assert [(row["resource_name"], row["hours"]) for row in listed.json()] == [("Jane", "7.50")]


def test_account_tasks_include_assignments_and_allocations(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    task = create_task(client, hierarchy["project"]["id"])
    resource = create_resource(client, account_id)
    client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"resource_name": "Jane Doe", "hours": "3"})
    allocation = client.post(
        f"/api/v1/tasks/{task['id']}/resource-allocations",
        json={"resource_id": resource["id"], "allocated_hours": "10"},
    )
    other_project = create_work_hierarchy(client)
    create_task(client, other_project["project"]["id"], name="Elsewhere")

    response = client.get(f"/api/v1/accounts/{account_id}/tasks")

    assert allocation.status_code == 201, allocation.json()
    assert response.status_code == 200
    [row] = response.json()
    assert row["id"] == task["id"]
    assert row["assignments"][0]["hours"] == "3.00"
    assert row["allocations"][0]["resource_name"] == "Jane Doe"
    assert row["allocations"][0]["allocated_hours"] == "10.00"


def test_account_sprints_listing(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    sprint = client.post(f"/api/v1/projects/{hierarchy['project']['id']}/sprints", json={"name": "Sprint 1"})
    create_work_hierarchy(client)

    response = client.get(f"/api/v1/accounts/{hierarchy['account']['id']}/sprints")

    assert sprint.status_code == 201, sprint.json()
    assert [row["id"] for row in response.json()] == [sprint.json()["id"]]


def test_account_time_off_listing(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    resource = create_resource(client, hierarchy["account"]["id"])
    created = client.post(
        f"/api/v1/resources/{resource['id']}/time-off",
        json={"start_date": "2026-10-12", "end_date": "2026-10-14", "reason": "Vacation"},
    )

    response = client.get(f"/api/v1/accounts/{hierarchy['account']['id']}/time-off")

    assert created.status_code == 201, created.json()
    [entry] = response.json()
    assert (entry["resource_name"], entry["reason"]) == ("Jane Doe", "Vacation")


def test_profile_photo_upload_sets_photo_url(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings, get_settings
    from app.main import app
    from app.services import attachments as attachment_service

    class FakeS3:
        def generate_presigned_url(self, operation: str, Params: dict, ExpiresIn: int, **_: object) -> str:
            return f"https://s3.test/{operation}/{Params['Key']}"

    monkeypatch.setattr(attachment_service, "get_s3_client", lambda settings: FakeS3())
    app.dependency_overrides[get_settings] = lambda: Settings(auth_mode="local", s3_bucket_name="bucket")

    upload = client.post("/api/v1/me/photo/presigned-upload", json={"file_name": "me.png", "content_type": "image/png"})
    me = client.get("/api/v1/me").json()
    cleared = client.patch("/api/v1/me", json={"remove_photo": True}).json()

    assert upload.status_code == 200, upload.json()
    assert upload.json()["upload_url"].startswith("https://s3.test/put_object/users/")
    assert me["photo_url"] == f"https://s3.test/get_object/{upload.json()['s3_key']}"
    assert cleared["photo_url"] is None


def test_comment_replies_and_attachment_summaries(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings, get_settings
    from app.main import app
    from app.services import attachments as attachment_service

    class FakeS3:
        def generate_presigned_url(self, operation: str, Params: dict, ExpiresIn: int, **_: object) -> str:
            return f"https://s3.test/{Params['Key']}"

    monkeypatch.setattr(attachment_service, "get_s3_client", lambda settings: FakeS3())
    app.dependency_overrides[get_settings] = lambda: Settings(auth_mode="local", s3_bucket_name="bucket")
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    other_task = create_task(client, hierarchy["project"]["id"], name="Other")

    parent = client.post(f"/api/v1/entities/TASK/{task['id']}/comments", json={"body": "Status?"}).json()
    reply = client.post(
        f"/api/v1/entities/TASK/{task['id']}/comments",
        json={"body": "On track", "parent_comment_id": parent["id"]},
    )
    misplaced = client.post(
        f"/api/v1/entities/TASK/{other_task['id']}/comments",
        json={"body": "Wrong thread", "parent_comment_id": parent["id"]},
    )
    client.post(
        f"/api/v1/entities/COMMENT/{parent['id']}/attachments/presigned-upload",
        json={"file_name": "plan.pdf", "content_type": "application/pdf"},
    )
    db_session.expire_all()  # each API request gets a fresh session in the app
    thread = client.get(f"/api/v1/entities/TASK/{task['id']}/comments").json()

    assert reply.status_code == 201, reply.json()
    assert reply.json()["parent_comment_id"] == parent["id"]
    assert misplaced.status_code == 400
    parent_in_thread = next(comment for comment in thread if comment["id"] == parent["id"])
    assert [attachment["file_name"] for attachment in parent_in_thread["attachments"]] == ["plan.pdf"]
