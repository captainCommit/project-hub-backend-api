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
from app.models.comment import Comment
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.services.auth import DEV_USER_EMAIL


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


def get_current_dev_user(db_session: Session) -> User:
    user = db_session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    assert user is not None
    return user


def test_member_can_add_comment_to_task(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.post(f"/api/v1/entities/TASK/{task['id']}/comments", json={"body": "Task note"})

    assert response.status_code == 201
    comment = response.json()
    assert comment["account_id"] == hierarchy["account"]["id"]
    assert comment["entity_type"] == "TASK"
    assert comment["entity_id"] == task["id"]
    assert comment["body"] == "Task note"


def test_member_can_add_comment_to_risk(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    risk = create_risk(client, hierarchy["project"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.post(f"/api/v1/entities/RISK/{risk['id']}/comments", json={"body": "Risk note"})

    assert response.status_code == 201
    comment = response.json()
    assert comment["account_id"] == hierarchy["account"]["id"]
    assert comment["entity_type"] == "RISK"
    assert comment["entity_id"] == risk["id"]
    assert comment["body"] == "Risk note"


def test_viewer_cannot_add_comment(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.post(f"/api/v1/entities/TASK/{task['id']}/comments", json={"body": "Blocked"})

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_viewer_can_read_comments(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    create_comment(client, "TASK", task["id"], "Visible")
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.get(f"/api/v1/entities/TASK/{task['id']}/comments")

    assert response.status_code == 200
    assert [comment["body"] for comment in response.json()] == ["Visible"]


def test_non_member_cannot_read_comments(client: TestClient, db_session: Session) -> None:
    other_user = User(email="comment-other@example.com", full_name="Comment Other")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(name="Other Comment Account", slug="other-comment-account", created_by=other_user.id)
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
    db_session.flush()
    db_session.add(
        Comment(
            account_id=other_account.id,
            entity_type="TASK",
            entity_id=task.id,
            body="Hidden",
            created_by=other_user.id,
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/entities/TASK/{task.id}/comments")

    assert response.status_code == 403
    assert response.json()["message"] == "Account access denied."


def test_user_can_edit_own_comment(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)
    comment = create_comment(client, "TASK", task["id"], "Original")

    response = client.patch(f"/api/v1/comments/{comment['id']}", json={"body": "Updated"})

    assert response.status_code == 200
    assert response.json()["body"] == "Updated"


def test_user_cannot_edit_another_users_comment_unless_owner_or_admin(
    client: TestClient,
    db_session: Session,
) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    other_user = User(email="comment-author@example.com", full_name="Comment Author")
    db_session.add(other_user)
    db_session.flush()
    comment = Comment(
        account_id=UUID(hierarchy["account"]["id"]),
        entity_type="TASK",
        entity_id=UUID(task["id"]),
        body="Other user's comment",
        created_by=other_user.id,
    )
    db_session.add(comment)
    db_session.commit()

    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)
    blocked_response = client.patch(f"/api/v1/comments/{comment.id}", json={"body": "Blocked update"})

    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.ADMIN)
    allowed_response = client.patch(f"/api/v1/comments/{comment.id}", json={"body": "Admin update"})

    assert blocked_response.status_code == 403
    assert blocked_response.json()["message"] == "Cannot modify another user's comment."
    assert allowed_response.status_code == 200
    assert allowed_response.json()["body"] == "Admin update"


def test_delete_removes_comment(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)
    comment = create_comment(client, "TASK", task["id"], "Delete me")

    delete_response = client.delete(f"/api/v1/comments/{comment['id']}")
    list_response = client.get(f"/api/v1/entities/TASK/{task['id']}/comments")

    assert delete_response.status_code == 204
    assert list_response.status_code == 200
    assert list_response.json() == []


def test_invalid_entity_type_is_rejected(client: TestClient) -> None:
    response = client.get(f"/api/v1/entities/UNKNOWN/{uuid4()}/comments")

    assert response.status_code == 400
    assert response.json()["message"] == "Unsupported comment entity type."


def test_nonexistent_entity_id_is_rejected(client: TestClient) -> None:
    response = client.get(f"/api/v1/entities/TASK/{uuid4()}/comments")

    assert response.status_code == 404
    assert response.json()["message"] == "Comment target not found."