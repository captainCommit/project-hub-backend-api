from collections.abc import Generator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
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
from app.models.user import User


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
    assert response.json() == {"detail": "Insufficient account role."}


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
    assert response.json() == {"detail": "Invalid task status."}


def test_parent_task_must_belong_to_same_project(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    other_hierarchy = create_work_hierarchy(client)
    parent = create_task(client, other_hierarchy["project"]["id"], "Other Parent")

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks",
        json={"name": "Child", "parent_task_id": parent["id"]},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Parent task must belong to the same project."}


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
    assert response.json() == {"detail": "Predecessor task must belong to the same project."}


def test_task_cannot_be_own_predecessor(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])

    response = client.post(
        f"/api/v1/tasks/{task['id']}/predecessors",
        json={"predecessor_task_id": task["id"]},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Task cannot be its own predecessor."}


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
    assert second.json() == {"detail": "Task predecessor already exists."}


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
    assert response.json() == {"detail": "Account access denied."}


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