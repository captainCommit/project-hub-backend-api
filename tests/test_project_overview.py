from collections.abc import Generator
from datetime import date, timedelta
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


def json_value(value: object) -> object:
    if isinstance(value, (UUID, date)):
        return str(value)
    return value


def create_account(client: TestClient, name: str = "Acme", slug: str = "acme") -> dict:
    response = client.post("/api/v1/accounts", json={"name": name, "slug": slug})
    assert response.status_code == 201
    return response.json()


def create_work_hierarchy(client: TestClient) -> dict[str, dict]:
    unique_suffix = uuid4().hex[:8]
    account = create_account(client, name=f"Overview {unique_suffix}", slug=f"overview-{unique_suffix}")
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
        json={"name": "Project", "description": "Project overview target"},
    )
    assert project_response.status_code == 201
    return {"account": account, "portfolio": portfolio, "program": program, "project": project_response.json()}


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
        json={"resource_id": resource_id, **extra},
    )
    assert response.status_code == 201
    return response.json()


def get_overview(client: TestClient, project_id: str) -> dict:
    response = client.get(f"/api/v1/projects/{project_id}/overview")
    assert response.status_code == 200
    return response.json()


def test_account_member_can_view_project_overview(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.get(f"/api/v1/projects/{hierarchy['project']['id']}/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["project"]["id"] == hierarchy["project"]["id"]
    assert body["project"]["account_id"] == hierarchy["account"]["id"]
    assert body["project"]["status"]["value"] == "ACTIVE"
    assert body["stats"] == {
        "total_tasks": 0,
        "completed_tasks": 0,
        "in_progress_tasks": 0,
        "overdue_tasks": 0,
        "upcoming_milestones": 0,
        "open_risks": 0,
        "open_issues": 0,
        "pending_decisions": 0,
        "open_assumptions": 0,
        "resource_count": 0,
        "overallocated_resources": 0,
    }
    assert body["health"]["schedule"] == "UNKNOWN"
    assert body["resource_summary"] == {
        "total_resources": 0,
        "total_allocated_hours": 0.0,
        "overallocated_resources": 0,
    }


def test_non_member_is_blocked_from_project_overview(client: TestClient, db_session: Session) -> None:
    other_user = User(email="overview-other@example.com", full_name="Overview Other")
    db_session.add(other_user)
    db_session.flush()
    account = Account(name="Private Overview", slug="private-overview", created_by=other_user.id)
    db_session.add(account)
    db_session.flush()
    db_session.add(AccountMember(account_id=account.id, user_id=other_user.id, role=AccountMemberRole.OWNER.value))
    portfolio = Portfolio(account_id=account.id, name="Private Portfolio", created_by=other_user.id)
    db_session.add(portfolio)
    db_session.flush()
    program = Program(account_id=account.id, portfolio_id=portfolio.id, name="Private Program", created_by=other_user.id)
    db_session.add(program)
    db_session.flush()
    project = Project(
        account_id=account.id,
        portfolio_id=portfolio.id,
        program_id=program.id,
        name="Private Project",
        created_by=other_user.id,
    )
    db_session.add(project)
    db_session.commit()

    response = client.get(f"/api/v1/projects/{project.id}/overview")

    assert response.status_code == 403
    assert response.json()["message"] == "Account access denied."


def test_stats_count_tasks_and_exclude_soft_deleted_tasks(client: TestClient, db_session: Session) -> None:
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
    milestone_type_id = get_option_id(db_session, account_id=account_id, entity_type="TASK", option_name="TYPE", value="MILESTONE")
    today = date.today()

    create_task(client, project_id, "Complete task", status_id=complete_status_id)
    create_task(client, project_id, "In progress task", status_id=in_progress_status_id)
    create_task(client, project_id, "Not started task")
    create_task(
        client,
        project_id,
        "Upcoming milestone",
        task_type_id=milestone_type_id,
        finish_date=today + timedelta(days=7),
    )
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

    body = get_overview(client, project_id)

    assert body["stats"]["total_tasks"] == 4
    assert body["stats"]["completed_tasks"] == 1
    assert body["stats"]["in_progress_tasks"] == 1
    assert body["stats"]["upcoming_milestones"] == 1


def test_overdue_task_sets_schedule_health_red(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    in_progress_status_id = get_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        entity_type="TASK",
        option_name="STATUS",
        value="IN_PROGRESS",
    )
    create_task(
        client,
        hierarchy["project"]["id"],
        "Late task",
        status_id=in_progress_status_id,
        finish_date=date.today() - timedelta(days=1),
    )

    body = get_overview(client, hierarchy["project"]["id"])

    assert body["stats"]["overdue_tasks"] == 1
    assert body["health"]["schedule"] == "RED"
    assert body["health"]["overall"] == "RED"


def test_high_priority_issue_sets_scope_health_red(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    high_priority_id = get_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        entity_type="ISSUE",
        option_name="PRIORITY",
        value="HIGH",
    )
    create_raid_item(client, hierarchy["project"]["id"], "issues", title="Escalated issue", priority_id=high_priority_id)

    body = get_overview(client, hierarchy["project"]["id"])

    assert body["stats"]["open_issues"] == 1
    assert body["health"]["scope"] == "RED"
    assert body["top_issues"][0]["title"] == "Escalated issue"
    assert body["top_issues"][0]["priority"]["value"] == "HIGH"


def test_overallocated_resource_sets_resource_health_red(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Allocated task")
    resource = create_resource(client, hierarchy["account"]["id"], "Engineer", weekly_capacity_hours=10)
    create_allocation(client, task["id"], resource["id"], allocated_hours=12)

    body = get_overview(client, hierarchy["project"]["id"])

    assert body["stats"]["resource_count"] == 1
    assert body["stats"]["overallocated_resources"] == 1
    assert body["health"]["resources"] == "RED"
    assert body["resource_summary"] == {
        "total_resources": 1,
        "total_allocated_hours": 12.0,
        "overallocated_resources": 1,
    }


def test_recent_activity_is_returned(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Activity task")

    body = get_overview(client, hierarchy["project"]["id"])

    assert body["recent_activity"]
    assert body["recent_activity"][0]["entity_type"] == "TASK"
    assert body["recent_activity"][0]["entity_id"] == task["id"]
    assert body["recent_activity"][0]["action"] == "CREATED"


def test_upcoming_milestones_are_limited_and_sorted(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    milestone_type_id = get_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        entity_type="TASK",
        option_name="TYPE",
        value="MILESTONE",
    )
    today = date.today()
    for offset in [10, 2, 7, 1, 5, 3]:
        create_task(
            client,
            hierarchy["project"]["id"],
            f"Milestone day {offset}",
            task_type_id=milestone_type_id,
            finish_date=today + timedelta(days=offset),
        )
    create_task(
        client,
        hierarchy["project"]["id"],
        "Milestone outside window",
        task_type_id=milestone_type_id,
        finish_date=today + timedelta(days=45),
    )

    body = get_overview(client, hierarchy["project"]["id"])

    assert body["stats"]["upcoming_milestones"] == 6
    assert [milestone["name"] for milestone in body["upcoming_milestones"]] == [
        "Milestone day 1",
        "Milestone day 2",
        "Milestone day 3",
        "Milestone day 5",
        "Milestone day 7",
    ]