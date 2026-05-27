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
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.resource import Resource
from app.models.resource_allocation import ResourceAllocation
from app.models.task import Task
from app.models.user import User


def trigram_tokens(value: object) -> set[str]:
    normalized = " ".join(str(value).lower().split())
    if not normalized:
        return set()
    padded = f"  {normalized} "
    return {padded[index : index + 3] for index in range(len(padded) - 2)}


def sqlite_similarity(left: object, right: object) -> float:
    if left is None or right is None:
        return 0.0
    left_value = str(left).lower()
    right_value = str(right).lower()
    if left_value == right_value:
        return 1.0
    left_trigrams = trigram_tokens(left_value)
    right_trigrams = trigram_tokens(right_value)
    if not left_trigrams or not right_trigrams:
        return 0.0
    return len(left_trigrams & right_trigrams) / len(left_trigrams | right_trigrams)


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


def create_work_hierarchy(
    client: TestClient,
    *,
    account_name: str | None = None,
    account_slug: str | None = None,
    program_name: str = "Program",
    project_name: str = "Project",
) -> dict[str, dict]:
    unique_suffix = uuid4().hex[:8]
    account = create_account(
        client,
        name=account_name or f"Acme {unique_suffix}",
        slug=account_slug or f"acme-{unique_suffix}",
    )
    portfolio_response = client.post(
        f"/api/v1/accounts/{account['id']}/portfolios",
        json={"name": "Portfolio"},
    )
    assert portfolio_response.status_code == 201
    portfolio = portfolio_response.json()
    program_response = client.post(
        f"/api/v1/portfolios/{portfolio['id']}/programs",
        json={"name": program_name},
    )
    assert program_response.status_code == 201
    program = program_response.json()
    project_response = client.post(
        f"/api/v1/programs/{program['id']}/projects",
        json={"name": project_name},
    )
    assert project_response.status_code == 201
    return {"account": account, "portfolio": portfolio, "program": program, "project": project_response.json()}


def create_task(client: TestClient, project_id: str, name: str = "Task", **extra: object) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/tasks", json={"name": name, **extra})
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


def set_current_user_role(db_session: Session, account_id: str | UUID, role: AccountMemberRole) -> None:
    membership = db_session.scalar(
        select(AccountMember).where(AccountMember.account_id == UUID(str(account_id)))
    )
    assert membership is not None
    membership.role = role.value
    db_session.commit()


def add_account_user(db_session: Session, *, account_id: str | UUID, email: str = "member@example.com") -> User:
    user = User(email=email, full_name="Account Member")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        AccountMember(
            account_id=UUID(str(account_id)),
            user_id=user.id,
            role=AccountMemberRole.MEMBER.value,
        )
    )
    db_session.commit()
    return user


def create_private_account(db_session: Session, *, slug: str = "private-resource-account") -> Account:
    other_user = User(email=f"{slug}@example.com", full_name="Private Owner")
    db_session.add(other_user)
    db_session.flush()
    account = Account(name=f"Private {slug}", slug=slug, created_by=other_user.id)
    db_session.add(account)
    db_session.flush()
    db_session.add(
        AccountMember(
            account_id=account.id,
            user_id=other_user.id,
            role=AccountMemberRole.OWNER.value,
        )
    )
    db_session.commit()
    return account


def test_manager_can_create_resource(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MANAGER)

    response = client.post(
        f"/api/v1/accounts/{hierarchy['account']['id']}/resources",
        json={"name": "Designer", "role": "UX", "weekly_capacity_hours": 32},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Designer"
    assert body["role"] == "UX"
    assert body["weekly_capacity_hours"] == "32.00"


def test_member_cannot_create_resource(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.post(f"/api/v1/accounts/{hierarchy['account']['id']}/resources", json={"name": "Blocked"})

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_resource_user_id_must_belong_to_account(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    other_user = User(email="outside@example.com", full_name="Outside User")
    db_session.add(other_user)
    db_session.commit()

    response = client.post(
        f"/api/v1/accounts/{hierarchy['account']['id']}/resources",
        json={"name": "Outside", "user_id": str(other_user.id)},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "User must belong to the account."


def test_resource_list_returns_active_resources(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    active = create_resource(client, hierarchy["account"]["id"], "Active Resource")
    inactive = create_resource(client, hierarchy["account"]["id"], "Inactive Resource")
    db_session.get(Resource, UUID(inactive["id"])).is_active = False
    db_session.commit()

    response = client.get(f"/api/v1/accounts/{hierarchy['account']['id']}/resources")

    assert response.status_code == 200
    assert [resource["id"] for resource in response.json()] == [active["id"]]


def test_delete_resource_sets_is_active_false(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    resource = create_resource(client, hierarchy["account"]["id"], "Deactivate Me")

    response = client.delete(f"/api/v1/resources/{resource['id']}")

    assert response.status_code == 204
    stored = db_session.get(Resource, UUID(resource["id"]))
    assert stored is not None
    assert stored.is_active is False


def test_inactive_resource_cannot_receive_allocation(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    resource = create_resource(client, hierarchy["account"]["id"], "Inactive Allocation Target")
    client.delete(f"/api/v1/resources/{resource['id']}")

    response = client.post(
        f"/api/v1/tasks/{task['id']}/resource-allocations",
        json={"resource_id": resource["id"], "allocated_hours": 4},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Inactive resources cannot receive allocations."


def test_member_can_create_allocation(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    resource = create_resource(client, hierarchy["account"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.post(
        f"/api/v1/tasks/{task['id']}/resource-allocations",
        json={"resource_id": resource["id"], "allocated_hours": 8, "start_date": "2026-01-01"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["resource_id"] == resource["id"]
    assert body["allocated_hours"] == "8.00"


def test_viewer_cannot_create_allocation(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    resource = create_resource(client, hierarchy["account"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.post(
        f"/api/v1/tasks/{task['id']}/resource-allocations",
        json={"resource_id": resource["id"], "allocated_hours": 8},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_task_resource_account_mismatch_is_rejected(client: TestClient) -> None:
    first = create_work_hierarchy(client, account_slug="first-resource-account")
    second = create_work_hierarchy(client, account_slug="second-resource-account")
    task = create_task(client, first["project"]["id"])
    resource = create_resource(client, second["account"]["id"], "Wrong Account Resource")

    response = client.post(
        f"/api/v1/tasks/{task['id']}/resource-allocations",
        json={"resource_id": resource["id"], "allocated_hours": 8},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Resource must belong to the account."


def test_invalid_allocation_date_range_rejected(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    resource = create_resource(client, hierarchy["account"]["id"])

    response = client.post(
        f"/api/v1/tasks/{task['id']}/resource-allocations",
        json={"resource_id": resource["id"], "start_date": "2026-01-10", "end_date": "2026-01-01"},
    )

    assert response.status_code == 422


def test_negative_allocated_hours_rejected(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    resource = create_resource(client, hierarchy["account"]["id"])

    response = client.post(
        f"/api/v1/tasks/{task['id']}/resource-allocations",
        json={"resource_id": resource["id"], "allocated_hours": -1},
    )

    assert response.status_code == 422


def test_allocation_update_works(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    resource = create_resource(client, hierarchy["account"]["id"])
    allocation = create_allocation(client, task["id"], resource["id"], allocated_hours=4)

    response = client.patch(f"/api/v1/resource-allocations/{allocation['id']}", json={"allocated_hours": 6})

    assert response.status_code == 200
    assert response.json()["allocated_hours"] == "6.00"


def test_allocation_delete_works(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    resource = create_resource(client, hierarchy["account"]["id"])
    allocation = create_allocation(client, task["id"], resource["id"], allocated_hours=4)

    response = client.delete(f"/api/v1/resource-allocations/{allocation['id']}")

    assert response.status_code == 204
    assert db_session.get(ResourceAllocation, UUID(allocation["id"])) is None


def test_account_member_can_view_resource_calendar(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Calendar Task")
    resource = create_resource(client, hierarchy["account"]["id"], "Calendar Resource", weekly_capacity_hours=40)
    create_allocation(client, task["id"], resource["id"], allocated_hours=10, start_date="2026-01-01", end_date="2026-01-07")
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.get(
        f"/api/v1/accounts/{hierarchy['account']['id']}/resource-calendar",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resources"][0]["resource"]["id"] == resource["id"]
    assert body["resources"][0]["allocations"][0]["task"]["name"] == "Calendar Task"


def test_non_member_blocked_from_resource_calendar(client: TestClient, db_session: Session) -> None:
    account = create_private_account(db_session, slug="calendar-private-account")

    response = client.get(
        f"/api/v1/accounts/{account.id}/resource-calendar",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Account access denied."


def test_calendar_filters_by_resource_id(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    first_task = create_task(client, hierarchy["project"]["id"], "First Task")
    second_task = create_task(client, hierarchy["project"]["id"], "Second Task")
    first_resource = create_resource(client, hierarchy["account"]["id"], "First Resource")
    second_resource = create_resource(client, hierarchy["account"]["id"], "Second Resource")
    create_allocation(client, first_task["id"], first_resource["id"], allocated_hours=4)
    create_allocation(client, second_task["id"], second_resource["id"], allocated_hours=4)

    response = client.get(
        f"/api/v1/accounts/{hierarchy['account']['id']}/resource-calendar",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31", "resource_id": first_resource["id"]},
    )

    assert response.status_code == 200
    resources = response.json()["resources"]
    assert len(resources) == 1
    assert resources[0]["resource"]["id"] == first_resource["id"]


def test_calendar_filters_by_project_id_and_program_id(client: TestClient) -> None:
    first = create_work_hierarchy(client, account_slug="calendar-filter-account", program_name="Alpha Program", project_name="Alpha Project")
    first_task = create_task(client, first["project"]["id"], "Alpha Task")
    first_resource = create_resource(client, first["account"]["id"], "Shared Resource")
    create_allocation(client, first_task["id"], first_resource["id"], allocated_hours=4)

    second_portfolio_response = client.post(
        f"/api/v1/accounts/{first['account']['id']}/portfolios",
        json={"name": "Second Portfolio"},
    )
    assert second_portfolio_response.status_code == 201
    second_program_response = client.post(
        f"/api/v1/portfolios/{second_portfolio_response.json()['id']}/programs",
        json={"name": "Beta Program"},
    )
    assert second_program_response.status_code == 201
    second_project_response = client.post(
        f"/api/v1/programs/{second_program_response.json()['id']}/projects",
        json={"name": "Beta Project"},
    )
    assert second_project_response.status_code == 201
    second_task = create_task(client, second_project_response.json()["id"], "Beta Task")
    create_allocation(client, second_task["id"], first_resource["id"], allocated_hours=4)

    project_response = client.get(
        f"/api/v1/accounts/{first['account']['id']}/resource-calendar",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31", "project_id": first["project"]["id"]},
    )
    program_response = client.get(
        f"/api/v1/accounts/{first['account']['id']}/resource-calendar",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31", "program_id": first["program"]["id"]},
    )

    assert project_response.status_code == 200
    assert program_response.status_code == 200
    assert project_response.json()["resources"][0]["allocations"][0]["task"]["name"] == "Alpha Task"
    assert program_response.json()["resources"][0]["allocations"][0]["task"]["name"] == "Alpha Task"


def test_calendar_overallocated_true_when_allocated_hours_exceed_capacity(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    resource = create_resource(client, hierarchy["account"]["id"], weekly_capacity_hours=10)
    create_allocation(client, task["id"], resource["id"], allocated_hours=12)

    response = client.get(
        f"/api/v1/accounts/{hierarchy['account']['id']}/resource-calendar",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert response.status_code == 200
    entry = response.json()["resources"][0]
    assert entry["total_allocated_hours"] == "12.00"
    assert entry["utilization_percent"] == 120.0
    assert entry["overallocated"] is True


def test_calendar_excludes_soft_deleted_tasks(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Deleted Calendar Task")
    resource = create_resource(client, hierarchy["account"]["id"], "Soft Delete Resource")
    create_allocation(client, task["id"], resource["id"], allocated_hours=12)
    delete_response = client.delete(f"/api/v1/tasks/{task['id']}")
    assert delete_response.status_code == 204

    response = client.get(
        f"/api/v1/accounts/{hierarchy['account']['id']}/resource-calendar",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31", "resource_id": resource["id"]},
    )

    assert response.status_code == 200
    entry = response.json()["resources"][0]
    assert entry["allocations"] == []
    assert entry["total_allocated_hours"] == "0"


def test_resource_can_be_found_in_global_search(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    resource = create_resource(client, hierarchy["account"]["id"], "Capacity Planner", role="Strategist")

    response = client.get("/api/v1/search", params={"q": "Strategist", "entity_types": "RESOURCE"})

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["entity_type"] == "RESOURCE"
    assert results[0]["id"] == resource["id"]
    assert results[0]["title"] == "Capacity Planner"