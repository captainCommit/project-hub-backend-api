from collections.abc import Generator
from datetime import date, timedelta
from decimal import Decimal
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
from app.models.resource_allocation import ResourceAllocation
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


def json_value(value: object) -> object:
    if isinstance(value, (UUID, date, Decimal)):
        return str(value)
    return value


def create_account(client: TestClient, name: str = "Dashboard", slug: str | None = None) -> dict:
    slug = slug or f"dashboard-{uuid4().hex[:8]}"
    response = client.post("/api/v1/accounts", json={"name": name, "slug": slug})
    assert response.status_code == 201
    return response.json()


def create_portfolio(client: TestClient, account_id: str, name: str = "Portfolio") -> dict:
    response = client.post(f"/api/v1/accounts/{account_id}/portfolios", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_program(client: TestClient, portfolio_id: str, name: str = "Program") -> dict:
    response = client.post(f"/api/v1/portfolios/{portfolio_id}/programs", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_project(client: TestClient, program_id: str, name: str = "Project", **extra: object) -> dict:
    response = client.post(
        f"/api/v1/programs/{program_id}/projects",
        json={"name": name, **{key: json_value(value) for key, value in extra.items()}},
    )
    assert response.status_code == 201
    return response.json()


def create_work_hierarchy(client: TestClient) -> dict[str, dict]:
    account = create_account(client)
    portfolio = create_portfolio(client, account["id"])
    program = create_program(client, portfolio["id"])
    project = create_project(client, program["id"])
    return {"account": account, "portfolio": portfolio, "program": program, "project": project}


def create_task(client: TestClient, project_id: str, name: str = "Task", **extra: object) -> dict:
    payload = {"name": name, **{key: json_value(value) for key, value in extra.items()}}
    response = client.post(f"/api/v1/projects/{project_id}/tasks", json=payload)
    assert response.status_code == 201
    return response.json()


def create_raid_item(client: TestClient, project_id: str, collection: str, **payload: object) -> dict:
    json_payload = {key: json_value(value) for key, value in payload.items()}
    response = client.post(f"/api/v1/projects/{project_id}/{collection}", json=json_payload)
    assert response.status_code == 201
    return response.json()


def create_resource(client: TestClient, account_id: str, name: str = "Resource", **extra: object) -> dict:
    response = client.post(f"/api/v1/accounts/{account_id}/resources", json={"name": name, **extra})
    assert response.status_code == 201
    return response.json()


def create_allocation(client: TestClient, task_id: str, resource_id: str, **extra: object) -> dict:
    response = client.post(
        f"/api/v1/tasks/{task_id}/resource-allocations",
        json={"resource_id": resource_id, **{key: json_value(value) for key, value in extra.items()}},
    )
    assert response.status_code == 201
    return response.json()


def get_option_id(
    db_session: Session,
    *,
    account_id: str | UUID,
    entity_type: str,
    option_name: str,
    value: str,
) -> UUID:
    option_id = db_session.scalar(
        select(OptionValue.id)
        .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
        .where(
            OptionSet.account_id == UUID(str(account_id)),
            OptionSet.entity_type == entity_type,
            OptionSet.name == option_name,
            OptionValue.value == value,
        )
    )
    assert option_id is not None
    return option_id


def set_current_user_role(db_session: Session, account_id: str | UUID, role: AccountMemberRole) -> None:
    membership = db_session.scalar(
        select(AccountMember).where(AccountMember.account_id == UUID(str(account_id)))
    )
    assert membership is not None
    membership.role = role.value
    db_session.commit()


def get_dashboard(client: TestClient, account_id: str, **params: object) -> dict:
    response = client.get(
        f"/api/v1/accounts/{account_id}/dashboard",
        params={key: str(value) for key, value in params.items() if value is not None},
    )
    assert response.status_code == 200
    return response.json()


def test_account_member_can_view_dashboard(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.get(f"/api/v1/accounts/{hierarchy['account']['id']}/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["portfolio_count"] == 1
    assert body["summary"]["program_count"] == 1
    assert body["summary"]["project_count"] == 1
    assert body["summary"]["active_project_count"] == 1
    assert body["health"] == {
        "overall": "GREEN",
        "schedule": "UNKNOWN",
        "scope": "GREEN",
        "resources": "UNKNOWN",
        "trend": "UNKNOWN",
    }


def test_non_member_is_blocked_from_dashboard(client: TestClient, db_session: Session) -> None:
    other_user = User(email="dashboard-private@example.com", full_name="Dashboard Private")
    db_session.add(other_user)
    db_session.flush()
    account = Account(name="Private Dashboard", slug="private-dashboard", created_by=other_user.id)
    db_session.add(account)
    db_session.flush()
    db_session.add(AccountMember(account_id=account.id, user_id=other_user.id, role=AccountMemberRole.OWNER.value))
    db_session.commit()

    response = client.get(f"/api/v1/accounts/{account.id}/dashboard")

    assert response.status_code == 403
    assert response.json()["message"] == "Account access denied."


def test_dashboard_filters_work(client: TestClient) -> None:
    account = create_account(client)
    first_portfolio = create_portfolio(client, account["id"], "Portfolio A")
    first_program = create_program(client, first_portfolio["id"], "Program A")
    first_project = create_project(client, first_program["id"], "Project A")
    second_project = create_project(client, first_program["id"], "Project B")
    second_portfolio = create_portfolio(client, account["id"], "Portfolio C")
    second_program = create_program(client, second_portfolio["id"], "Program C")
    create_project(client, second_program["id"], "Project C")

    account_dashboard = get_dashboard(client, account["id"])
    portfolio_dashboard = get_dashboard(client, account["id"], portfolio_id=first_portfolio["id"])
    program_dashboard = get_dashboard(client, account["id"], program_id=first_program["id"])
    project_dashboard = get_dashboard(client, account["id"], project_id=second_project["id"])

    assert account_dashboard["summary"]["portfolio_count"] == 2
    assert account_dashboard["summary"]["program_count"] == 2
    assert account_dashboard["summary"]["project_count"] == 3
    assert portfolio_dashboard["summary"]["portfolio_count"] == 1
    assert portfolio_dashboard["summary"]["program_count"] == 1
    assert portfolio_dashboard["summary"]["project_count"] == 2
    assert program_dashboard["summary"]["portfolio_count"] == 1
    assert program_dashboard["summary"]["program_count"] == 1
    assert program_dashboard["summary"]["project_count"] == 2
    assert project_dashboard["summary"]["portfolio_count"] == 1
    assert project_dashboard["summary"]["program_count"] == 1
    assert project_dashboard["summary"]["project_count"] == 1

    mismatch = client.get(
        f"/api/v1/accounts/{account['id']}/dashboard",
        params={"program_id": first_program["id"], "project_id": first_project["id"]},
    )
    assert mismatch.status_code == 200


def test_dashboard_counts_tasks_raid_resources_and_excludes_soft_deleted_tasks(
    client: TestClient,
    db_session: Session,
) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    complete_status_id = get_option_id(db_session, account_id=account_id, entity_type="TASK", option_name="STATUS", value="COMPLETE")
    in_progress_status_id = get_option_id(
        db_session,
        account_id=account_id,
        entity_type="TASK",
        option_name="STATUS",
        value="IN_PROGRESS",
    )
    high_risk_priority_id = get_option_id(
        db_session,
        account_id=account_id,
        entity_type="RISK",
        option_name="PRIORITY",
        value="HIGH",
    )
    high_issue_priority_id = get_option_id(
        db_session,
        account_id=account_id,
        entity_type="ISSUE",
        option_name="PRIORITY",
        value="HIGH",
    )
    today = date.today()
    complete_task = create_task(client, project_id, "Complete task", status_id=complete_status_id)
    overdue_task = create_task(
        client,
        project_id,
        "Overdue task",
        status_id=in_progress_status_id,
        finish_date=today - timedelta(days=2),
    )
    create_task(client, project_id, "Future task", status_id=in_progress_status_id, finish_date=today + timedelta(days=5))
    create_raid_item(client, project_id, "risks", title="Open risk", priority_id=high_risk_priority_id)
    create_raid_item(client, project_id, "issues", title="Open issue", priority_id=high_issue_priority_id)
    create_raid_item(client, project_id, "decisions", title="Pending decision")
    resource = create_resource(client, account_id, "Engineer", weekly_capacity_hours=10)
    create_allocation(client, overdue_task["id"], resource["id"], allocated_hours=12)

    db_session.add(
        Task(
            account_id=UUID(account_id),
            project_id=UUID(project_id),
            status_id=complete_status_id,
            name="Soft deleted task",
            is_deleted=True,
        )
    )
    db_session.commit()

    body = get_dashboard(client, account_id)

    assert body["summary"]["total_tasks"] == 3
    assert body["summary"]["completed_tasks"] == 1
    assert body["summary"]["overdue_tasks"] == 1
    assert body["summary"]["open_risks"] == 1
    assert body["summary"]["open_issues"] == 1
    assert body["summary"]["pending_decisions"] == 1
    assert body["summary"]["overallocated_resources"] == 1
    assert body["overdue_tasks"][0]["id"] == overdue_task["id"]
    assert all(task["id"] != complete_task["id"] for task in body["overdue_tasks"])
    assert body["top_risks"][0]["title"] == "Open risk"
    assert body["top_issues"][0]["title"] == "Open issue"
    assert body["resource_utilization"][0]["resource"]["name"] == "Engineer"
    assert body["resource_utilization"][0]["allocated_hours"] == "12.00"
    assert body["resource_utilization"][0]["overallocated"] is True


def test_dashboard_health_calculation_and_projects_at_risk(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    in_progress_status_id = get_option_id(
        db_session,
        account_id=account_id,
        entity_type="TASK",
        option_name="STATUS",
        value="IN_PROGRESS",
    )
    create_task(
        client,
        project_id,
        "Critical late task",
        status_id=in_progress_status_id,
        finish_date=date.today() - timedelta(days=1),
    )

    body = get_dashboard(client, account_id)

    assert body["health"]["schedule"] == "RED"
    assert body["health"]["overall"] == "RED"
    assert body["health"]["trend"] == "DECLINING"
    assert body["summary"]["at_risk_project_count"] == 1
    assert body["projects_at_risk"][0]["id"] == project_id
    assert body["projects_at_risk"][0]["health"]["overall"] == "RED"
    assert body["projects_at_risk"][0]["health"]["trend"] == "DECLINING"
    assert body["projects_at_risk"][0]["overdue_tasks"] == 1


def test_dashboard_health_trend_reports_stable_and_improving(
    client: TestClient,
    db_session: Session,
) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    complete_status_id = get_option_id(
        db_session,
        account_id=account_id,
        entity_type="TASK",
        option_name="STATUS",
        value="COMPLETE",
    )

    create_task(client, project_id, "Future stable task", finish_date=date.today() + timedelta(days=5))
    stable_body = get_dashboard(client, account_id)

    assert stable_body["health"]["trend"] == "STABLE"

    create_task(
        client,
        project_id,
        "Recently completed late task",
        status_id=complete_status_id,
        finish_date=date.today() - timedelta(days=1),
    )
    improving_body = get_dashboard(client, account_id)

    assert improving_body["health"]["trend"] == "IMPROVING"


def test_dashboard_recent_activity_returned(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Activity task")

    body = get_dashboard(client, hierarchy["account"]["id"])

    assert body["recent_activity"]
    assert body["recent_activity"][0]["entity_type"] == "TASK"
    assert body["recent_activity"][0]["entity_id"] == task["id"]
    assert body["recent_activity"][0]["action"] == "CREATED"


def test_dashboard_soft_deleted_task_allocations_are_excluded(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    resource = create_resource(client, account_id, "Designer", weekly_capacity_hours=10)
    deleted_task = Task(
        account_id=UUID(account_id),
        project_id=UUID(project_id),
        name="Deleted allocated task",
        finish_date=date.today() - timedelta(days=1),
        is_deleted=True,
    )
    db_session.add(deleted_task)
    db_session.flush()
    db_session.add(
        ResourceAllocation(
            account_id=UUID(account_id),
            task_id=deleted_task.id,
            resource_id=UUID(resource["id"]),
            allocated_hours=Decimal("12"),
        )
    )
    db_session.commit()

    body = get_dashboard(client, account_id)

    assert body["summary"]["total_tasks"] == 0
    assert body["summary"]["overdue_tasks"] == 0
    assert body["summary"]["overallocated_resources"] == 0
    assert body["overdue_tasks"] == []
    assert body["resource_utilization"] == []