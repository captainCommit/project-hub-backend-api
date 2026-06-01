from datetime import date, timedelta
from collections.abc import Generator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
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
from app.models.sprint import Sprint
from app.models.user import User


def sqlite_similarity(left: object, right: object) -> float:
    if left is None or right is None:
        return 0.0
    left_value = str(left).lower()
    right_value = str(right).lower()
    if left_value == right_value:
        return 1.0
    return 0.5 if right_value in left_value or left_value in right_value else 0.0


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_similarity(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.create_function("similarity", 2, sqlite_similarity)

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


def create_work_hierarchy(client: TestClient, *, project_payload: dict[str, object] | None = None) -> dict[str, dict]:
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
    payload = {"name": "Project", **(project_payload or {})}
    project_response = client.post(f"/api/v1/programs/{program['id']}/projects", json=payload)
    assert project_response.status_code == 201
    return {"account": account, "portfolio": portfolio, "program": program, "project": project_response.json()}


def create_task(client: TestClient, project_id: str, name: str = "Task", **extra: object) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/tasks", json={"name": name, **extra})
    assert response.status_code == 201
    return response.json()


def create_sprint(client: TestClient, project_id: str, name: str = "Sprint 1", **extra: object) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/sprints", json={"name": name, **extra})
    assert response.status_code == 201
    return response.json()


def set_current_user_role(db_session: Session, account_id: str | UUID, role: AccountMemberRole) -> None:
    membership = db_session.scalar(select(AccountMember).where(AccountMember.account_id == UUID(str(account_id))))
    assert membership is not None
    membership.role = role.value
    db_session.commit()


def get_option_id(db_session: Session, *, account_id: str | UUID, entity_type: str, name: str, value: str) -> UUID:
    option_id = db_session.scalar(
        select(OptionValue.id)
        .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
        .where(
            OptionSet.account_id == UUID(str(account_id)),
            OptionSet.entity_type == entity_type,
            OptionSet.name == name,
            OptionValue.value == value,
        )
    )
    assert option_id is not None
    return option_id


def test_existing_project_creation_defaults_delivery_type_to_waterfall(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    assert hierarchy["project"]["delivery_type"] == "WATERFALL"


def test_project_can_be_created_with_agile_delivery_type(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client, project_payload={"delivery_type": "AGILE"})

    assert hierarchy["project"]["delivery_type"] == "AGILE"


def test_invalid_delivery_type_is_rejected(client: TestClient) -> None:
    account = create_account(client, name="Delivery", slug="delivery")
    portfolio_response = client.post(f"/api/v1/accounts/{account['id']}/portfolios", json={"name": "Portfolio"})
    assert portfolio_response.status_code == 201
    program_response = client.post(
        f"/api/v1/portfolios/{portfolio_response.json()['id']}/programs",
        json={"name": "Program"},
    )
    assert program_response.status_code == 201

    response = client.post(
        f"/api/v1/programs/{program_response.json()['id']}/projects",
        json={"name": "Bad Project", "delivery_type": "SCRUM"},
    )

    assert response.status_code == 422


def test_new_accounts_seed_sprint_status_options(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Sprint Options", slug="sprint-options")

    values = db_session.scalars(
        select(OptionValue.value)
        .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
        .where(
            OptionSet.account_id == UUID(account["id"]),
            OptionSet.entity_type == "SPRINT",
            OptionSet.name == "STATUS",
        )
        .order_by(OptionValue.sort_order)
    ).all()

    assert list(values) == ["PLANNED", "ACTIVE", "COMPLETED", "CANCELLED"]


def test_new_accounts_seed_task_priority_options(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Task Priority Options", slug="task-priority-options")

    values = db_session.scalars(
        select(OptionValue.value)
        .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
        .where(
            OptionSet.account_id == UUID(account["id"]),
            OptionSet.entity_type == "TASK",
            OptionSet.name == "PRIORITY",
        )
        .order_by(OptionValue.sort_order)
    ).all()

    assert list(values) == ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def test_member_can_create_sprint(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/sprints",
        json={"name": "Sprint Alpha", "goal": "Deliver increment"},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Sprint Alpha"
    assert response.json()["goal"] == "Deliver increment"


def test_viewer_cannot_create_sprint(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.post(f"/api/v1/projects/{hierarchy['project']['id']}/sprints", json={"name": "Blocked"})

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_sprint_gets_default_status(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    default_status_id = get_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        entity_type="SPRINT",
        name="STATUS",
        value="PLANNED",
    )

    sprint = create_sprint(client, hierarchy["project"]["id"])

    assert sprint["status_id"] == str(default_status_id)
    assert sprint["status"]["value"] == "PLANNED"


def test_invalid_sprint_date_range_rejected(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/sprints",
        json={"name": "Bad Sprint", "start_date": "2026-02-10", "end_date": "2026-02-01"},
    )

    assert response.status_code == 422


def test_task_can_be_assigned_to_sprint(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    sprint = create_sprint(client, hierarchy["project"]["id"], "Sprint Beta")
    task = create_task(client, hierarchy["project"]["id"], "Sprint Task", sprint_id=sprint["id"])

    assert task["sprint_id"] == sprint["id"]
    assert task["sprint"]["id"] == sprint["id"]
    assert task["sprint"]["name"] == "Sprint Beta"


def test_task_create_and_update_accept_story_points_and_priority(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    high_priority_id = get_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        entity_type="TASK",
        name="PRIORITY",
        value="HIGH",
    )
    critical_priority_id = get_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        entity_type="TASK",
        name="PRIORITY",
        value="CRITICAL",
    )

    task = create_task(
        client,
        hierarchy["project"]["id"],
        "Estimated Story",
        story_points=3,
        priority_id=str(high_priority_id),
    )
    update_response = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"story_points": 8, "priority_id": str(critical_priority_id)},
    )

    assert task["story_points"] == 3
    assert task["priority_id"] == str(high_priority_id)
    assert task["priority"]["value"] == "HIGH"
    assert update_response.status_code == 200
    assert update_response.json()["story_points"] == 8
    assert update_response.json()["priority_id"] == str(critical_priority_id)
    assert update_response.json()["priority"]["value"] == "CRITICAL"


def test_invalid_story_points_rejected(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks",
        json={"name": "Invalid Estimate", "story_points": 0},
    )

    assert response.status_code == 422


def test_invalid_task_priority_rejected(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    risk_priority_id = get_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        entity_type="RISK",
        name="PRIORITY",
        value="HIGH",
    )

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks",
        json={"name": "Bad Priority", "priority_id": str(risk_priority_id)},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Invalid task priority."


def test_task_cannot_be_assigned_to_sprint_from_another_project(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    other_hierarchy = create_work_hierarchy(client)
    other_sprint = create_sprint(client, other_hierarchy["project"]["id"], "Other Sprint")

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/tasks",
        json={"name": "Wrong Sprint", "sprint_id": other_sprint["id"]},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Sprint must belong to the same project."


def test_sprint_metrics_total_completed_and_days_remaining(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    complete_status_id = get_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        entity_type="TASK",
        name="STATUS",
        value="COMPLETE",
    )
    sprint_end = date.today() + timedelta(days=7)
    sprint = create_sprint(
        client,
        hierarchy["project"]["id"],
        "Metrics Sprint",
        start_date=str(date.today()),
        end_date=str(sprint_end),
    )
    create_task(
        client,
        hierarchy["project"]["id"],
        "Done Story",
        sprint_id=sprint["id"],
        story_points=5,
        status_id=str(complete_status_id),
    )
    create_task(client, hierarchy["project"]["id"], "Remaining Story", sprint_id=sprint["id"], story_points=5)
    create_task(client, hierarchy["project"]["id"], "Unestimated Story", sprint_id=sprint["id"])

    response = client.get(f"/api/v1/sprints/{sprint['id']}/metrics")

    assert response.status_code == 200
    metrics = response.json()
    assert metrics["sprint"]["id"] == sprint["id"]
    assert metrics["total_tasks"] == 3
    assert metrics["total_story_points"] == 10
    assert metrics["completed_story_points"] == 5
    assert metrics["remaining_story_points"] == 5
    assert metrics["completion_percent"] == 50
    assert metrics["days_remaining"] == 7
    assert metrics["story_point_legend"][0] == {"points": 1, "label": "< 2 hrs"}


def test_delivery_overview_counts_and_filters_by_program_and_project(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    active_sprint_status_id = get_option_id(
        db_session,
        account_id=account_id,
        entity_type="SPRINT",
        name="STATUS",
        value="ACTIVE",
    )
    complete_task_status_id = get_option_id(
        db_session,
        account_id=account_id,
        entity_type="TASK",
        name="STATUS",
        value="COMPLETE",
    )
    second_program_response = client.post(
        f"/api/v1/portfolios/{hierarchy['portfolio']['id']}/programs",
        json={"name": "Second Program"},
    )
    assert second_program_response.status_code == 201
    second_program = second_program_response.json()
    second_project_response = client.post(
        f"/api/v1/programs/{second_program['id']}/projects",
        json={"name": "Second Project"},
    )
    assert second_project_response.status_code == 201
    second_project = second_project_response.json()

    active_sprint = create_sprint(
        client,
        hierarchy["project"]["id"],
        "Active Sprint",
        status_id=str(active_sprint_status_id),
    )
    planned_sprint = create_sprint(client, second_project["id"], "Planned Sprint")
    create_task(
        client,
        hierarchy["project"]["id"],
        "Done Story",
        sprint_id=active_sprint["id"],
        story_points=3,
        status_id=str(complete_task_status_id),
    )
    create_task(client, hierarchy["project"]["id"], "Open Story", sprint_id=active_sprint["id"], story_points=2)
    create_task(client, second_project["id"], "Second Open Story", sprint_id=planned_sprint["id"], story_points=5)

    overview_response = client.get(f"/api/v1/accounts/{account_id}/delivery/overview")
    program_response = client.get(
        f"/api/v1/accounts/{account_id}/delivery/overview",
        params={"program_id": hierarchy["program"]["id"]},
    )
    project_response = client.get(
        f"/api/v1/accounts/{account_id}/delivery/overview",
        params={"project_id": second_project["id"]},
    )

    assert overview_response.status_code == 200
    overview = overview_response.json()
    assert overview["total_sprints"] == 2
    assert overview["active_sprints"] == 1
    assert overview["total_stories"] == 3
    assert overview["done_stories"] == 1

    assert program_response.status_code == 200
    program_overview = program_response.json()
    assert program_overview["total_sprints"] == 1
    assert program_overview["active_sprints"] == 1
    assert program_overview["total_stories"] == 2
    assert program_overview["done_stories"] == 1
    assert program_overview["programs"][0]["id"] == hierarchy["program"]["id"]
    assert program_overview["programs"][0]["projects"][0]["sprints"][0]["total_story_points"] == 5
    assert program_overview["programs"][0]["projects"][0]["sprints"][0]["completed_story_points"] == 3

    assert project_response.status_code == 200
    project_overview = project_response.json()
    assert project_overview["total_sprints"] == 1
    assert project_overview["active_sprints"] == 0
    assert project_overview["total_stories"] == 1
    assert project_overview["done_stories"] == 0
    assert project_overview["programs"][0]["projects"][0]["id"] == second_project["id"]


def test_programs_projects_consolidated_endpoint_returns_tree(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client, project_payload={"delivery_type": "HYBRID"})

    response = client.get(f"/api/v1/accounts/{hierarchy['account']['id']}/programs-projects")

    assert response.status_code == 200
    portfolio = response.json()["portfolios"][0]
    assert portfolio["id"] == hierarchy["portfolio"]["id"]
    assert portfolio["programs"][0]["id"] == hierarchy["program"]["id"]
    assert portfolio["programs"][0]["project_count"] == 1
    assert portfolio["programs"][0]["projects"][0]["delivery_type"] == "HYBRID"


def test_search_returns_sprints(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    sprint = create_sprint(client, hierarchy["project"]["id"], "Sprint Search Target", goal="Checkout flow")

    response = client.get("/api/v1/search", params={"q": "Checkout", "entity_types": "SPRINT"})

    assert response.status_code == 200
    assert response.json()["results"][0]["id"] == sprint["id"]
    assert response.json()["results"][0]["entity_type"] == "SPRINT"


def test_existing_account_can_receive_sprint_status_options(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Legacy", slug="legacy")
    option_set = db_session.scalar(
        select(OptionSet).where(
            OptionSet.account_id == UUID(account["id"]),
            OptionSet.entity_type == "SPRINT",
            OptionSet.name == "STATUS",
        )
    )
    assert option_set is not None
    db_session.query(OptionValue).filter(OptionValue.option_set_id == option_set.id).delete()
    db_session.delete(option_set)
    db_session.commit()

    from app.services.options import OptionService

    OptionService(db_session).seed_defaults_for_account(UUID(account["id"]))
    db_session.commit()

    restored = db_session.scalar(
        select(OptionSet).where(
            OptionSet.account_id == UUID(account["id"]),
            OptionSet.entity_type == "SPRINT",
            OptionSet.name == "STATUS",
        )
    )
    assert restored is not None