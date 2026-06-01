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


def create_account(client: TestClient, name: str = "Skills", slug: str | None = None) -> dict:
    response = client.post(
        "/api/v1/accounts",
        json={"name": name, "slug": slug or f"skills-{uuid4().hex[:8]}"},
    )
    assert response.status_code == 201
    return response.json()


def create_work_hierarchy(client: TestClient) -> dict[str, dict]:
    account = create_account(client)
    portfolio_response = client.post(f"/api/v1/accounts/{account['id']}/portfolios", json={"name": "Portfolio"})
    assert portfolio_response.status_code == 201
    portfolio = portfolio_response.json()
    program_response = client.post(f"/api/v1/portfolios/{portfolio['id']}/programs", json={"name": "Program"})
    assert program_response.status_code == 201
    program = program_response.json()
    project_response = client.post(f"/api/v1/programs/{program['id']}/projects", json={"name": "Project"})
    assert project_response.status_code == 201
    return {"account": account, "portfolio": portfolio, "program": program, "project": project_response.json()}


def create_skill(client: TestClient, account_id: str, name: str = "Python", **extra: object) -> dict:
    response = client.post(f"/api/v1/accounts/{account_id}/skills", json={"name": name, **extra})
    assert response.status_code == 201
    return response.json()


def create_resource(client: TestClient, account_id: str, name: str = "Resource") -> dict:
    response = client.post(f"/api/v1/accounts/{account_id}/resources", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_task(client: TestClient, project_id: str, name: str = "Task") -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/tasks", json={"name": name})
    assert response.status_code == 201
    return response.json()


def set_current_user_role(db_session: Session, account_id: str | UUID, role: AccountMemberRole) -> None:
    membership = db_session.scalar(
        select(AccountMember).where(AccountMember.account_id == UUID(str(account_id)))
    )
    assert membership is not None
    membership.role = role.value
    db_session.commit()


def create_private_hierarchy(db_session: Session) -> dict[str, object]:
    owner = User(email=f"private-skills-{uuid4().hex[:8]}@example.com", full_name="Private Owner")
    db_session.add(owner)
    db_session.flush()
    account = Account(name="Private Skills", slug=f"private-skills-{uuid4().hex[:8]}", created_by=owner.id)
    db_session.add(account)
    db_session.flush()
    db_session.add(AccountMember(account_id=account.id, user_id=owner.id, role=AccountMemberRole.OWNER.value))
    db_session.commit()
    return {"account": account, "owner": owner}


def test_manager_can_create_skill(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MANAGER)

    response = client.post(
        f"/api/v1/accounts/{hierarchy['account']['id']}/skills",
        json={"name": "Python", "category": "Engineering"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Python"
    assert body["category"] == "Engineering"
    assert body["is_active"] is True


def test_member_cannot_create_skill(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.post(f"/api/v1/accounts/{hierarchy['account']['id']}/skills", json={"name": "Blocked"})

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_skill_list_excludes_inactive_by_default(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    active_skill = create_skill(client, hierarchy["account"]["id"], "Active Skill")
    inactive_skill = create_skill(client, hierarchy["account"]["id"], "Inactive Skill")
    delete_response = client.delete(f"/api/v1/skills/{inactive_skill['id']}")
    assert delete_response.status_code == 204

    default_response = client.get(f"/api/v1/accounts/{hierarchy['account']['id']}/skills")
    include_inactive_response = client.get(
        f"/api/v1/accounts/{hierarchy['account']['id']}/skills",
        params={"include_inactive": True},
    )

    assert default_response.status_code == 200
    assert [skill["id"] for skill in default_response.json()] == [active_skill["id"]]
    assert include_inactive_response.status_code == 200
    assert {skill["id"] for skill in include_inactive_response.json()} == {active_skill["id"], inactive_skill["id"]}


def test_resource_skill_assignment_works(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    skill = create_skill(client, hierarchy["account"]["id"], "Architecture")
    resource = create_resource(client, hierarchy["account"]["id"], "Architect")

    response = client.post(
        f"/api/v1/resources/{resource['id']}/skills",
        json={"skill_id": skill["id"], "proficiency": "ADVANCED"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["resource_id"] == resource["id"]
    assert body["skill_id"] == skill["id"]
    assert body["proficiency"] == "ADVANCED"
    assert body["skill"]["name"] == "Architecture"

    list_response = client.get(f"/api/v1/resources/{resource['id']}/skills")
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == body["id"]


def test_duplicate_resource_skill_rejected(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    skill = create_skill(client, hierarchy["account"]["id"], "Testing")
    resource = create_resource(client, hierarchy["account"]["id"], "Tester")
    first_response = client.post(
        f"/api/v1/resources/{resource['id']}/skills",
        json={"skill_id": skill["id"], "proficiency": "INTERMEDIATE"},
    )
    assert first_response.status_code == 201

    duplicate_response = client.post(
        f"/api/v1/resources/{resource['id']}/skills",
        json={"skill_id": skill["id"], "proficiency": "EXPERT"},
    )

    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["message"] == "Resource skill already exists."


def test_task_required_skill_assignment_works(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    skill = create_skill(client, hierarchy["account"]["id"], "Data Modeling")
    task = create_task(client, hierarchy["project"]["id"], "Design schema")

    response = client.post(
        f"/api/v1/tasks/{task['id']}/required-skills",
        json={"skill_id": skill["id"], "required_proficiency": "EXPERT"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["task_id"] == task["id"]
    assert body["skill_id"] == skill["id"]
    assert body["required_proficiency"] == "EXPERT"
    assert body["skill"]["name"] == "Data Modeling"

    list_response = client.get(f"/api/v1/tasks/{task['id']}/required-skills")
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == body["id"]


def test_duplicate_required_skill_rejected(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    skill = create_skill(client, hierarchy["account"]["id"], "Security")
    task = create_task(client, hierarchy["project"]["id"], "Threat model")
    first_response = client.post(
        f"/api/v1/tasks/{task['id']}/required-skills",
        json={"skill_id": skill["id"], "required_proficiency": "ADVANCED"},
    )
    assert first_response.status_code == 201

    duplicate_response = client.post(
        f"/api/v1/tasks/{task['id']}/required-skills",
        json={"skill_id": skill["id"], "required_proficiency": "EXPERT"},
    )

    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["message"] == "Task required skill already exists."


def test_cross_account_skill_resource_task_rejected(client: TestClient) -> None:
    first = create_work_hierarchy(client)
    second = create_work_hierarchy(client)
    first_skill = create_skill(client, first["account"]["id"], "First Account Skill")
    second_skill = create_skill(client, second["account"]["id"], "Second Account Skill")
    first_resource = create_resource(client, first["account"]["id"], "First Resource")
    first_task = create_task(client, first["project"]["id"], "First Task")

    resource_response = client.post(
        f"/api/v1/resources/{first_resource['id']}/skills",
        json={"skill_id": second_skill["id"], "proficiency": "BEGINNER"},
    )
    task_response = client.post(
        f"/api/v1/tasks/{first_task['id']}/required-skills",
        json={"skill_id": second_skill["id"], "required_proficiency": "BEGINNER"},
    )

    assert resource_response.status_code == 400
    assert resource_response.json()["message"] == "Skill must belong to the account."
    assert task_response.status_code == 400
    assert task_response.json()["message"] == "Skill must belong to the account."
    assert first_skill["account_id"] == first["account"]["id"]


def test_viewer_cannot_mutate_skills_or_assignments(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    skill = create_skill(client, hierarchy["account"]["id"], "Viewer Skill")
    resource = create_resource(client, hierarchy["account"]["id"], "Viewer Resource")
    task = create_task(client, hierarchy["project"]["id"], "Viewer Task")
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    create_response = client.post(f"/api/v1/accounts/{hierarchy['account']['id']}/skills", json={"name": "Blocked"})
    update_response = client.patch(f"/api/v1/skills/{skill['id']}", json={"name": "Blocked"})
    delete_response = client.delete(f"/api/v1/skills/{skill['id']}")
    resource_skill_response = client.post(
        f"/api/v1/resources/{resource['id']}/skills",
        json={"skill_id": skill["id"], "proficiency": "BEGINNER"},
    )
    task_required_skill_response = client.post(
        f"/api/v1/tasks/{task['id']}/required-skills",
        json={"skill_id": skill["id"], "required_proficiency": "BEGINNER"},
    )

    assert create_response.status_code == 403
    assert update_response.status_code == 403
    assert delete_response.status_code == 403
    assert resource_skill_response.status_code == 403
    assert task_required_skill_response.status_code == 403


def test_inactive_skill_cannot_be_assigned(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    skill = create_skill(client, hierarchy["account"]["id"], "Inactive Assignment")
    resource = create_resource(client, hierarchy["account"]["id"], "Resource")
    task = create_task(client, hierarchy["project"]["id"], "Task")
    delete_response = client.delete(f"/api/v1/skills/{skill['id']}")
    assert delete_response.status_code == 204

    resource_response = client.post(
        f"/api/v1/resources/{resource['id']}/skills",
        json={"skill_id": skill["id"], "proficiency": "BEGINNER"},
    )
    task_response = client.post(
        f"/api/v1/tasks/{task['id']}/required-skills",
        json={"skill_id": skill["id"], "required_proficiency": "BEGINNER"},
    )

    assert resource_response.status_code == 400
    assert resource_response.json()["message"] == "Inactive skills cannot be assigned."
    assert task_response.status_code == 400
    assert task_response.json()["message"] == "Inactive skills cannot be assigned."


def test_soft_deleted_task_cannot_receive_required_skills(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    skill = create_skill(client, hierarchy["account"]["id"], "Deleted Task Skill")
    deleted_task = Task(
        account_id=UUID(hierarchy["account"]["id"]),
        project_id=UUID(hierarchy["project"]["id"]),
        name="Deleted task",
        is_deleted=True,
    )
    db_session.add(deleted_task)
    db_session.commit()

    response = client.post(
        f"/api/v1/tasks/{deleted_task.id}/required-skills",
        json={"skill_id": skill["id"], "required_proficiency": "BEGINNER"},
    )

    assert response.status_code == 404
    assert response.json()["message"] == "Task not found."


def test_skill_added_to_global_search(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    skill = create_skill(client, hierarchy["account"]["id"], "Kubernetes", category="Platform")

    response = client.get("/api/v1/search", params={"q": "Kubernetes", "entity_types": "SKILL"})

    assert response.status_code == 200
    assert response.json()["results"][0] == {
        "entity_type": "SKILL",
        "id": skill["id"],
        "title": "Kubernetes",
        "subtitle": hierarchy["account"]["name"],
        "score": 1.0,
    }


def test_non_member_cannot_read_private_skill(client: TestClient, db_session: Session) -> None:
    private = create_private_hierarchy(db_session)
    response = client.get(f"/api/v1/accounts/{private['account'].id}/skills")

    assert response.status_code == 403
    assert response.json()["message"] == "Account access denied."