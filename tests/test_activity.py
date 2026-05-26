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


def create_task(client: TestClient, project_id: str, name: str = "Task") -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/tasks", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_risk(client: TestClient, project_id: str, title: str = "Risk") -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/risks", json={"title": title})
    assert response.status_code == 201
    return response.json()


def create_comment(client: TestClient, entity_type: str, entity_id: str, body: str = "Comment") -> dict:
    response = client.post(f"/api/v1/entities/{entity_type}/{entity_id}/comments", json={"body": body})
    assert response.status_code == 201
    return response.json()


def set_current_user_role(db_session: Session, account_id: str | UUID, role: AccountMemberRole) -> None:
    membership = db_session.scalar(
        select(AccountMember).where(AccountMember.account_id == UUID(str(account_id)))
    )
    assert membership is not None
    membership.role = role.value
    db_session.commit()


def test_creating_task_creates_activity_record(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    task = create_task(client, hierarchy["project"]["id"], "Activity Task")
    response = client.get(f"/api/v1/entities/TASK/{task['id']}/activity")

    assert response.status_code == 200
    activity = response.json()
    assert activity[0]["entity_type"] == "TASK"
    assert activity[0]["entity_id"] == task["id"]
    assert activity[0]["action"] == "CREATED"
    assert activity[0]["new_values"]["name"] == "Activity Task"


def test_updating_task_creates_activity_record(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Original Task")

    update_response = client.patch(f"/api/v1/tasks/{task['id']}", json={"name": "Updated Task"})
    activity_response = client.get(f"/api/v1/entities/TASK/{task['id']}/activity")

    assert update_response.status_code == 200
    assert activity_response.status_code == 200
    activity = activity_response.json()
    assert activity[0]["action"] == "UPDATED"
    assert activity[0]["old_values"]["name"] == "Original Task"
    assert activity[0]["new_values"]["name"] == "Updated Task"


def test_creating_risk_creates_activity_record(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)

    risk = create_risk(client, hierarchy["project"]["id"], "Activity Risk")
    response = client.get(f"/api/v1/entities/RISK/{risk['id']}/activity")

    assert response.status_code == 200
    activity = response.json()
    assert activity[0]["entity_type"] == "RISK"
    assert activity[0]["entity_id"] == risk["id"]
    assert activity[0]["action"] == "CREATED"
    assert activity[0]["new_values"]["title"] == "Activity Risk"


def test_creating_comment_creates_commented_activity_record(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])

    comment = create_comment(client, "TASK", task["id"], "Activity comment")
    response = client.get(f"/api/v1/entities/TASK/{task['id']}/activity")

    assert response.status_code == 200
    activity = response.json()
    assert activity[0]["action"] == "COMMENTED"
    assert activity[0]["new_values"]["comment_id"] == comment["id"]
    assert activity[0]["new_values"]["body"] == "Activity comment"


def test_non_member_cannot_read_activity(client: TestClient, db_session: Session) -> None:
    other_user = User(email="activity-other@example.com", full_name="Activity Other")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(name="Other Activity Account", slug="other-activity-account", created_by=other_user.id)
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
    task = Task(account_id=other_account.id, project_id=project.id, name="Other Task", created_by=other_user.id)
    db_session.add(task)
    db_session.commit()

    response = client.get(f"/api/v1/entities/TASK/{task.id}/activity")

    assert response.status_code == 403
    assert response.json() == {"detail": "Account access denied."}


def test_account_member_can_read_activity(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.get(f"/api/v1/entities/TASK/{task['id']}/activity")

    assert response.status_code == 200
    assert response.json()[0]["action"] == "CREATED"


def test_project_activity_includes_task_and_raid_activity(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Project Activity Task")
    risk = create_risk(client, hierarchy["project"]["id"], "Project Activity Risk")

    response = client.get(f"/api/v1/projects/{hierarchy['project']['id']}/activity")

    assert response.status_code == 200
    activity = response.json()
    entity_pairs = {(item["entity_type"], item["entity_id"]) for item in activity}
    assert ("TASK", task["id"]) in entity_pairs
    assert ("RISK", risk["id"]) in entity_pairs


def test_activity_records_are_ordered_newest_first(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Ordered Task")
    client.patch(f"/api/v1/tasks/{task['id']}", json={"name": "Ordered Task Updated"})

    response = client.get(f"/api/v1/entities/TASK/{task['id']}/activity")

    assert response.status_code == 200
    activity = response.json()
    assert [item["action"] for item in activity[:2]] == ["UPDATED", "CREATED"]