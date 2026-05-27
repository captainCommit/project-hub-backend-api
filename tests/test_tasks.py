from collections.abc import Generator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.request_context import REQUEST_ID_HEADER
from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.account import Account
from app.models.account_member import AccountMember, AccountMemberRole
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.task import Task
from app.models.task_assignment import TaskAssignment
from app.models.task_predecessor import TaskPredecessor
from app.models.user import User
from tests.helpers import assert_error_response


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        class_=Session,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def override_get_settings() -> Settings:
        return Settings(auth_mode="local")

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = override_get_settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def create_account(client: TestClient, name: str = "Acme", slug: str = "acme") -> dict:
    response = client.post("/api/v1/accounts", json={"name": name, "slug": slug})
    assert response.status_code == 201
    return response.json()


def create_work_hierarchy(client: TestClient) -> dict[str, dict]:
    unique_suffix = uuid4().hex[:8]
    account = create_account(client, name=f"Acme {unique_suffix}", slug=f"acme-{unique_suffix}")
    portfolio_response = client.post(
        f"/api/v1/accounts/{account['id']}/portfolios",
        json={"name": "Portfolio"},
    )
    assert portfolio_response.status_code == 201
    portfolio = portfolio_response.json()
    program_response = client.post(
        f"/api/v1/portfolios/{portfolio['id']}/programs",
        json={"name": "Program"},
    )
    assert program_response.status_code == 201
    program = program_response.json()
    project_response = client.post(
        f"/api/v1/programs/{program['id']}/projects",
        json={"name": "Project"},
    )
    assert project_response.status_code == 201
    return {"account": account, "portfolio": portfolio, "program": program, "project": project_response.json()}


def create_task(client: TestClient, project_id: str, name: str = "Task", **extra: object) -> dict:
    payload = {"name": name, **extra}
    response = client.post(f"/api/v1/projects/{project_id}/tasks", json=payload)
    assert response.status_code == 201
    return response.json()


def create_sprint(client: TestClient, project_id: str, name: str = "Sprint", **extra: object) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/sprints", json={"name": name, **extra})
    assert response.status_code == 201
    return response.json()


def set_current_user_role(db_session: Session, account_id: str | UUID, role: AccountMemberRole) -> None:
    membership = db_session.scalar(
        select(AccountMember).where(AccountMember.account_id == UUID(str(account_id)))
    )
    assert membership is not None
    membership.role = role.value
    db_session.commit()


def get_task_option_id(
    db_session: Session,
    *,
    account_id: str | UUID,
    option_name: str,
    value: str,
) -> UUID:
    option_id = db_session.scalar(
        select(OptionValue.id)
        .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
        .where(
            OptionSet.account_id == UUID(str(account_id)),
            OptionSet.entity_type == "TASK",
            OptionSet.name == option_name,
            OptionValue.value == value,
        )
    )
    assert option_id is not None
    return option_id


def test_member_can_create_task(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks",
        json={"name": "Member Task"},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Member Task"


def test_viewer_cannot_create_task(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks",
        json={"name": "Blocked Task"},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_request_id_header_is_returned(client: TestClient) -> None:
    response = client.get("/", headers={REQUEST_ID_HEADER: "phase8-request-id"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "phase8-request-id"


def test_404_uses_consistent_error_shape(client: TestClient) -> None:
    assert_error_response(
        client.get(f"/api/v1/tasks/{uuid4()}"),
        status_code=404,
        error_code="NOT_FOUND",
        message="Task not found.",
    )


def test_validation_error_uses_consistent_error_shape(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    response = client.post(f"/api/v1/projects/{hierarchy['project']['id']}/tasks", json={"name": ""})

    body = assert_error_response(
        response,
        status_code=422,
        error_code="VALIDATION_ERROR",
        message="Validation error.",
    )
    assert "details" in body


def test_tasks_pagination_filtering_and_sort_validation(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    not_started_id = get_task_option_id(
        db_session,
        account_id=account_id,
        option_name="STATUS",
        value="NOT_STARTED",
    )
    complete_id = get_task_option_id(
        db_session,
        account_id=account_id,
        option_name="STATUS",
        value="COMPLETE",
    )
    type_id = get_task_option_id(
        db_session,
        account_id=account_id,
        option_name="TYPE",
        value="WORK_PACKAGE",
    )
    create_task(client, project_id, "Second", sort_order=20, status_id=str(not_started_id), task_type_id=str(type_id))
    create_task(client, project_id, "First", sort_order=10, status_id=str(complete_id), task_type_id=str(type_id))

    page_response = client.get(f"/api/v1/projects/{project_id}/tasks?paginated=true&page=1&page_size=1")
    filter_response = client.get(f"/api/v1/projects/{project_id}/tasks?status_id={complete_id}")
    invalid_page_size_response = client.get(f"/api/v1/projects/{project_id}/tasks?page_size=101")
    invalid_sort_response = client.get(f"/api/v1/projects/{project_id}/tasks?sort=drop_table")

    assert page_response.status_code == 200
    page = page_response.json()
    assert page["page"] == 1
    assert page["page_size"] == 1
    assert page["total"] == 2
    assert [item["name"] for item in page["items"]] == ["First"]

    assert filter_response.status_code == 200
    assert [item["name"] for item in filter_response.json()] == ["First"]

    assert_error_response(
        invalid_page_size_response,
        status_code=422,
        error_code="VALIDATION_ERROR",
        message="Validation error.",
    )
    assert_error_response(
        invalid_sort_response,
        status_code=400,
        error_code="BAD_REQUEST",
        message="Invalid sort field: drop_table.",
    )


def test_task_gets_default_status_and_type_when_omitted(
    client: TestClient,
    db_session: Session,
) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    default_status_id = get_task_option_id(
        db_session,
        account_id=account_id,
        option_name="STATUS",
        value="NOT_STARTED",
    )
    default_type_id = get_task_option_id(
        db_session,
        account_id=account_id,
        option_name="TYPE",
        value="WORK_PACKAGE",
    )

    task = create_task(client, hierarchy["project"]["id"])

    assert task["status_id"] == str(default_status_id)
    assert task["task_type_id"] == str(default_type_id)
    assert task["status"]["value"] == "NOT_STARTED"
    assert task["task_type"]["value"] == "WORK_PACKAGE"


def test_invalid_status_from_wrong_option_set_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    hierarchy = create_work_hierarchy(client)
    task_type_id = get_task_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        option_name="TYPE",
        value="WORK_PACKAGE",
    )

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks",
        json={"name": "Bad Status", "status_id": str(task_type_id)},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Invalid task status."


def test_parent_task_must_belong_to_same_project(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    other_hierarchy = create_work_hierarchy(client)
    parent = create_task(client, other_hierarchy["project"]["id"], "Other Parent")

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks",
        json={"name": "Child", "parent_task_id": parent["id"]},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Parent task must belong to the same project."


def test_percent_complete_outside_range_is_rejected(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks",
        json={"name": "Invalid Percent", "percent_complete": 101},
    )

    assert response.status_code == 422


def test_finish_date_before_start_date_is_rejected(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks",
        json={"name": "Invalid Dates", "start_date": "2026-02-10", "finish_date": "2026-02-01"},
    )

    assert response.status_code == 422


def test_task_assignment_requires_user_id_or_resource_name(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])

    response = client.post(f"/api/v1/tasks/{task['id']}/assignments", json={})

    assert response.status_code == 422


def test_can_create_assignment_with_resource_name(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])

    response = client.post(
        f"/api/v1/tasks/{task['id']}/assignments",
        json={"resource_name": "External Vendor"},
    )

    assert response.status_code == 201
    assert response.json()["resource_name"] == "External Vendor"


def test_predecessor_must_belong_to_same_project(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    other_hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Task")
    other_task = create_task(client, other_hierarchy["project"]["id"], "Other Task")

    response = client.post(
        f"/api/v1/tasks/{task['id']}/predecessors",
        json={"predecessor_task_id": other_task["id"]},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Predecessor task must belong to the same project."


def test_task_cannot_be_own_predecessor(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])

    response = client.post(
        f"/api/v1/tasks/{task['id']}/predecessors",
        json={"predecessor_task_id": task["id"]},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Task cannot be its own predecessor."


def test_duplicate_predecessor_is_blocked(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    predecessor = create_task(client, hierarchy["project"]["id"], "Predecessor")
    task = create_task(client, hierarchy["project"]["id"], "Successor")

    first = client.post(
        f"/api/v1/tasks/{task['id']}/predecessors",
        json={"predecessor_task_id": predecessor["id"]},
    )
    second = client.post(
        f"/api/v1/tasks/{task['id']}/predecessors",
        json={"predecessor_task_id": predecessor["id"]},
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["message"] == "Task predecessor already exists."


def test_non_member_cannot_read_tasks(client: TestClient, db_session: Session) -> None:
    other_user = User(email="task-other@example.com", full_name="Task Other")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(name="Other Task Account", slug="other-task-account", created_by=other_user.id)
    db_session.add(other_account)
    db_session.flush()
    db_session.add(
        AccountMember(
            account_id=other_account.id,
            user_id=other_user.id,
            role=AccountMemberRole.OWNER.value,
        )
    )
    portfolio = Portfolio(account_id=other_account.id, name="Other Portfolio", created_by=other_user.id)
    db_session.add(portfolio)
    db_session.flush()
    program = Program(
        account_id=other_account.id,
        portfolio_id=portfolio.id,
        name="Other Program",
        created_by=other_user.id,
    )
    db_session.add(program)
    db_session.flush()
    project = Project(
        account_id=other_account.id,
        portfolio_id=portfolio.id,
        program_id=program.id,
        name="Other Project",
        created_by=other_user.id,
    )
    db_session.add(project)
    db_session.commit()

    response = client.get(f"/api/v1/projects/{project.id}/tasks")

    assert response.status_code == 403
    assert response.json()["message"] == "Account access denied."


def test_task_list_includes_assignments_and_predecessors(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    predecessor = create_task(client, hierarchy["project"]["id"], "Predecessor", sort_order=1)
    task = create_task(client, hierarchy["project"]["id"], "Successor", sort_order=2)
    assignment_response = client.post(
        f"/api/v1/tasks/{task['id']}/assignments",
        json={"resource_name": "Designer"},
    )
    predecessor_response = client.post(
        f"/api/v1/tasks/{task['id']}/predecessors",
        json={"predecessor_task_id": predecessor["id"], "dependency_type": "FS"},
    )
    assert assignment_response.status_code == 201
    assert predecessor_response.status_code == 201

    response = client.get(f"/api/v1/projects/{hierarchy['project']['id']}/tasks")

    assert response.status_code == 200
    tasks = response.json()
    successor = next(item for item in tasks if item["id"] == task["id"])
    assert successor["assignments"][0]["resource_name"] == "Designer"
    assert successor["predecessors"][0]["predecessor_task_id"] == predecessor["id"]


def test_task_tree_returns_nested_tasks(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    parent = create_task(client, hierarchy["project"]["id"], "Parent")
    child = create_task(client, hierarchy["project"]["id"], "Child", parent_task_id=parent["id"])

    response = client.get(f"/api/v1/projects/{hierarchy['project']['id']}/tasks/tree")

    assert response.status_code == 200
    tree = response.json()
    assert tree[0]["id"] == parent["id"]
    assert tree[0]["children"][0]["id"] == child["id"]


def test_member_can_bulk_update_tasks(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    task = create_task(client, project_id, "Original")
    second_task = create_task(client, project_id, "Second")
    sprint = create_sprint(client, project_id)
    status_id = get_task_option_id(
        db_session,
        account_id=account_id,
        option_name="STATUS",
        value="IN_PROGRESS",
    )
    task_type_id = get_task_option_id(
        db_session,
        account_id=account_id,
        option_name="TYPE",
        value="MILESTONE",
    )
    parent = create_task(client, project_id, "Parent")
    set_current_user_role(db_session, account_id, AccountMemberRole.MEMBER)

    response = client.patch(
        f"/api/v1/projects/{project_id}/tasks/bulk",
        json={
            "updates": [
                {
                    "id": task["id"],
                    "fields": {
                        "name": "Updated Task",
                        "description": "Updated description",
                        "status_id": str(status_id),
                        "task_type_id": str(task_type_id),
                        "start_date": "2026-01-01",
                        "finish_date": "2026-01-10",
                        "duration_days": 5,
                        "percent_complete": 50,
                        "assigned_to": None,
                        "sprint_id": sprint["id"],
                        "parent_task_id": parent["id"],
                        "sort_order": 15,
                    },
                },
                {"id": second_task["id"], "fields": {"percent_complete": 25}},
            ]
        },
    )

    assert response.status_code == 200
    updated = {item["id"]: item for item in response.json()}
    assert updated[task["id"]]["name"] == "Updated Task"
    assert updated[task["id"]]["description"] == "Updated description"
    assert updated[task["id"]]["status_id"] == str(status_id)
    assert updated[task["id"]]["task_type_id"] == str(task_type_id)
    assert updated[task["id"]]["sprint_id"] == sprint["id"]
    assert updated[task["id"]]["parent_task_id"] == parent["id"]
    assert updated[task["id"]]["sort_order"] == "15.00"
    assert updated[second_task["id"]]["percent_complete"] == "25.00"


def test_viewer_cannot_bulk_update_tasks(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Blocked")
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.patch(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks/bulk",
        json={"updates": [{"id": task["id"], "fields": {"name": "Nope"}}]},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_bulk_update_rolls_back_if_one_task_is_invalid(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    first = create_task(client, project_id, "First")
    second = create_task(client, project_id, "Second")
    invalid_status_id = get_task_option_id(
        db_session,
        account_id=account_id,
        option_name="TYPE",
        value="WORK_PACKAGE",
    )
    set_current_user_role(db_session, account_id, AccountMemberRole.MEMBER)

    response = client.patch(
        f"/api/v1/projects/{project_id}/tasks/bulk",
        json={
            "updates": [
                {"id": first["id"], "fields": {"name": "Should Roll Back"}},
                {"id": second["id"], "fields": {"status_id": str(invalid_status_id)}},
            ]
        },
    )
    db_session.expire_all()
    first_after = db_session.get(Task, UUID(first["id"]))
    second_after = db_session.get(Task, UUID(second["id"]))

    assert response.status_code == 400
    assert response.json()["message"] == "Invalid task status."
    assert first_after is not None and first_after.name == "First"
    assert second_after is not None and second_after.name == "Second"


def test_bulk_update_rejects_task_from_another_project(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    other_hierarchy = create_work_hierarchy(client)
    other_task = create_task(client, other_hierarchy["project"]["id"], "Other")
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.patch(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks/bulk",
        json={"updates": [{"id": other_task["id"], "fields": {"name": "Wrong Project"}}]},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "All tasks must belong to the project."


def test_bulk_update_rejects_invalid_status_id(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Task")
    invalid_status_id = get_task_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        option_name="TYPE",
        value="WORK_PACKAGE",
    )

    response = client.patch(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks/bulk",
        json={"updates": [{"id": task["id"], "fields": {"status_id": str(invalid_status_id)}}]},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Invalid task status."


def test_bulk_update_rejects_invalid_sprint_id(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    other_hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Task")
    other_sprint = create_sprint(client, other_hierarchy["project"]["id"])

    response = client.patch(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks/bulk",
        json={"updates": [{"id": task["id"], "fields": {"sprint_id": other_sprint["id"]}}]},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Sprint must belong to the same project."


def test_member_can_bulk_delete_tasks(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    predecessor = create_task(client, project_id, "Predecessor", sort_order=1)
    parent = create_task(client, project_id, "Parent", sort_order=2)
    kept = create_task(client, project_id, "Kept", sort_order=3)
    assignment_response = client.post(
        f"/api/v1/tasks/{parent['id']}/assignments",
        json={"resource_name": "Designer"},
    )
    predecessor_response = client.post(
        f"/api/v1/tasks/{parent['id']}/predecessors",
        json={"predecessor_task_id": predecessor["id"]},
    )
    assert assignment_response.status_code == 201
    assert predecessor_response.status_code == 201
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)
    current_user = db_session.scalar(select(User).where(User.email == "dev@example.com"))
    assert current_user is not None

    response = client.request(
        "DELETE",
        f"/api/v1/projects/{project_id}/tasks/bulk",
        json={"task_ids": [parent["id"], predecessor["id"]]},
    )
    db_session.expire_all()
    parent_after = db_session.get(Task, UUID(parent["id"]))
    predecessor_after = db_session.get(Task, UUID(predecessor["id"]))
    kept_after = db_session.get(Task, UUID(kept["id"]))
    assignment = db_session.get(TaskAssignment, UUID(assignment_response.json()["id"]))
    task_predecessor = db_session.get(TaskPredecessor, UUID(predecessor_response.json()["id"]))
    list_response = client.get(f"/api/v1/projects/{project_id}/tasks")
    read_response = client.get(f"/api/v1/tasks/{parent['id']}")

    assert response.status_code == 204
    assert parent_after is not None
    assert parent_after.is_deleted is True
    assert parent_after.deleted_at is not None
    assert parent_after.deleted_by == current_user.id
    assert predecessor_after is not None and predecessor_after.is_deleted is True
    assert kept_after is not None and kept_after.name == "Kept"
    assert assignment is not None and assignment.task_id == parent_after.id
    assert task_predecessor is not None and task_predecessor.task_id == parent_after.id
    assert list_response.status_code == 200
    assert {task["id"] for task in list_response.json()} == {kept["id"]}
    assert read_response.status_code == 404
    assert read_response.json()["message"] == "Task not found."


def test_bulk_delete_rejects_task_with_non_deleted_children(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    parent = create_task(client, project_id, "Parent")
    child = create_task(client, project_id, "Child", parent_task_id=parent["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.request(
        "DELETE",
        f"/api/v1/projects/{project_id}/tasks/bulk",
        json={"task_ids": [parent["id"]]},
    )
    db_session.expire_all()
    parent_after = db_session.get(Task, UUID(parent["id"]))
    child_after = db_session.get(Task, UUID(child["id"]))

    assert response.status_code == 400
    assert response.json()["message"] == "Cannot delete task with non-deleted children."
    assert parent_after is not None and parent_after.is_deleted is False
    assert child_after is not None and child_after.is_deleted is False


def test_viewer_cannot_bulk_delete_tasks(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Blocked")
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.request(
        "DELETE",
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks/bulk",
        json={"task_ids": [task["id"]]},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_non_member_cannot_bulk_delete_tasks(client: TestClient, db_session: Session) -> None:
    other_user = User(email="task-bulk-delete-other@example.com", full_name="Task Bulk Delete Other")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(name="Other Delete Account", slug="other-delete-account", created_by=other_user.id)
    db_session.add(other_account)
    db_session.flush()
    db_session.add(
        AccountMember(
            account_id=other_account.id,
            user_id=other_user.id,
            role=AccountMemberRole.OWNER.value,
        )
    )
    portfolio = Portfolio(account_id=other_account.id, name="Other Portfolio", created_by=other_user.id)
    db_session.add(portfolio)
    db_session.flush()
    program = Program(
        account_id=other_account.id,
        portfolio_id=portfolio.id,
        name="Other Program",
        created_by=other_user.id,
    )
    db_session.add(program)
    db_session.flush()
    project = Project(
        account_id=other_account.id,
        portfolio_id=portfolio.id,
        program_id=program.id,
        name="Other Project",
        created_by=other_user.id,
    )
    db_session.add(project)
    db_session.flush()
    task = Task(account_id=other_account.id, project_id=project.id, name="Private Task", created_by=other_user.id)
    db_session.add(task)
    db_session.commit()

    response = client.request(
        "DELETE",
        f"/api/v1/projects/{project.id}/tasks/bulk",
        json={"task_ids": [str(task.id)]},
    )
    db_session.expire_all()
    task_after = db_session.get(Task, task.id)

    assert response.status_code == 403
    assert response.json()["message"] == "Account access denied."
    assert task_after is not None and task_after.is_deleted is False


def test_bulk_delete_rejects_task_from_another_project(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    other_hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Keep")
    other_task = create_task(client, other_hierarchy["project"]["id"], "Other")
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.request(
        "DELETE",
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks/bulk",
        json={"task_ids": [task["id"], other_task["id"]]},
    )
    db_session.expire_all()

    assert response.status_code == 400
    assert response.json()["message"] == "All tasks must belong to the project."
    assert db_session.get(Task, UUID(task["id"])) is not None


def test_bulk_delete_rejects_duplicate_task_ids(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Duplicate")

    response = client.request(
        "DELETE",
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks/bulk",
        json={"task_ids": [task["id"], task["id"]]},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Duplicate task id."


def test_single_task_delete_soft_deletes_task(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Single Delete")
    current_user = db_session.scalar(select(User).where(User.email == "dev@example.com"))
    assert current_user is not None

    response = client.delete(f"/api/v1/tasks/{task['id']}")
    db_session.expire_all()
    task_after = db_session.get(Task, UUID(task["id"]))
    list_response = client.get(f"/api/v1/projects/{hierarchy['project']['id']}/tasks")
    read_response = client.get(f"/api/v1/tasks/{task['id']}")

    assert response.status_code == 204
    assert task_after is not None
    assert task_after.is_deleted is True
    assert task_after.deleted_at is not None
    assert task_after.deleted_by == current_user.id
    assert list_response.status_code == 200
    assert list_response.json() == []
    assert read_response.status_code == 404
    assert read_response.json()["message"] == "Task not found."


def test_member_can_reorder_tasks(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    parent = create_task(client, project_id, "Parent", sort_order=1)
    child = create_task(client, project_id, "Child", sort_order=2)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.post(
        f"/api/v1/projects/{project_id}/tasks/reorder",
        json={
            "tasks": [
                {"id": parent["id"], "parent_task_id": None, "sort_order": 10},
                {"id": child["id"], "parent_task_id": parent["id"], "sort_order": 1},
            ]
        },
    )

    assert response.status_code == 200
    updated = {item["id"]: item for item in response.json()}
    assert updated[parent["id"]]["sort_order"] == "10.00"
    assert updated[child["id"]]["parent_task_id"] == parent["id"]


def test_viewer_cannot_reorder_tasks(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Task")
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks/reorder",
        json={"tasks": [{"id": task["id"], "parent_task_id": None, "sort_order": 5}]},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_reorder_is_atomic(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    first = create_task(client, project_id, "First", sort_order=1)
    second = create_task(client, project_id, "Second", sort_order=2)

    response = client.post(
        f"/api/v1/projects/{project_id}/tasks/reorder",
        json={
            "tasks": [
                {"id": first["id"], "parent_task_id": None, "sort_order": 10},
                {"id": second["id"], "parent_task_id": second["id"], "sort_order": 20},
            ]
        },
    )
    db_session.expire_all()
    first_after = db_session.get(Task, UUID(first["id"]))

    assert response.status_code == 400
    assert response.json()["message"] == "Task cannot be its own parent."
    assert first_after is not None and first_after.sort_order == Decimal("1.00")


def test_reorder_rejects_self_parenting(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Task")

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks/reorder",
        json={"tasks": [{"id": task["id"], "parent_task_id": task["id"], "sort_order": 1}]},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Task cannot be its own parent."


def test_reorder_rejects_circular_parent(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    parent = create_task(client, hierarchy["project"]["id"], "Parent")
    child = create_task(client, hierarchy["project"]["id"], "Child", parent_task_id=parent["id"])

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks/reorder",
        json={"tasks": [{"id": parent["id"], "parent_task_id": child["id"], "sort_order": 1}]},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Task hierarchy cannot contain circular parent references."


def test_move_task_works(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    parent = create_task(client, hierarchy["project"]["id"], "Parent")
    task = create_task(client, hierarchy["project"]["id"], "Task")

    response = client.post(
        f"/api/v1/tasks/{task['id']}/move",
        json={"parent_task_id": parent["id"], "sort_order": 5},
    )

    assert response.status_code == 200
    assert response.json()["parent_task_id"] == parent["id"]
    assert response.json()["sort_order"] == "5.00"


def test_indent_makes_previous_sibling_parent(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    previous = create_task(client, project_id, "Previous", sort_order=1)
    task = create_task(client, project_id, "Task", sort_order=2)

    response = client.post(f"/api/v1/tasks/{task['id']}/indent")

    assert response.status_code == 200
    assert response.json()["parent_task_id"] == previous["id"]


def test_indent_without_previous_sibling_returns_400(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "First", sort_order=1)

    response = client.post(f"/api/v1/tasks/{task['id']}/indent")

    assert response.status_code == 400
    assert response.json()["message"] == "Cannot indent task without a previous sibling."


def test_outdent_moves_task_one_level_up(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    grandparent = create_task(client, project_id, "Grandparent", sort_order=1)
    parent = create_task(client, project_id, "Parent", parent_task_id=grandparent["id"], sort_order=2)
    task = create_task(client, project_id, "Task", parent_task_id=parent["id"], sort_order=1)

    response = client.post(f"/api/v1/tasks/{task['id']}/outdent")
    db_session.expire_all()
    task_after = db_session.get(Task, UUID(task["id"]))

    assert response.status_code == 200
    assert response.json()["parent_task_id"] == grandparent["id"]
    assert task_after is not None and task_after.sort_order > Decimal("2.00")


def test_outdent_root_task_returns_400(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Root")

    response = client.post(f"/api/v1/tasks/{task['id']}/outdent")

    assert response.status_code == 400
    assert response.json()["message"] == "Cannot outdent a root task."


def test_cross_project_parent_rejected_for_move(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    other_hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Task")
    other_parent = create_task(client, other_hierarchy["project"]["id"], "Other Parent")

    response = client.post(
        f"/api/v1/tasks/{task['id']}/move",
        json={"parent_task_id": other_parent["id"], "sort_order": 1},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Parent task must belong to the same project."