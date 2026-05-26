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
from app.models.account_member import AccountMember, AccountMemberRole
from app.models.comment_mention import CommentMention
from app.models.notification import Notification
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.user import User
from app.services.auth import DEV_USER_EMAIL, get_current_user


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


def create_comment(client: TestClient, entity_type: str, entity_id: str, body: str = "Comment") -> dict:
    response = client.post(f"/api/v1/entities/{entity_type}/{entity_id}/comments", json={"body": body})
    assert response.status_code == 201
    return response.json()


def create_member(
    db_session: Session,
    *,
    account_id: str | UUID,
    email: str = "member@example.com",
    full_name: str = "Member User",
    role: AccountMemberRole = AccountMemberRole.MEMBER,
) -> User:
    user = User(email=email, full_name=full_name)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        AccountMember(
            account_id=UUID(str(account_id)),
            user_id=user.id,
            role=role.value,
        )
    )
    db_session.commit()
    return user


def get_dev_user(db_session: Session) -> User:
    user = db_session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    assert user is not None
    return user


def override_current_user(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def get_approved_decision_status_id(db_session: Session, account_id: str | UUID) -> UUID:
    status_id = db_session.scalar(
        select(OptionValue.id)
        .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
        .where(
            OptionSet.account_id == UUID(str(account_id)),
            OptionSet.entity_type == "DECISION",
            OptionSet.name == "STATUS",
            OptionValue.value == "APPROVED",
        )
    )
    assert status_id is not None
    return status_id


def notification_types(db_session: Session, user: User) -> list[str]:
    return list(
        db_session.scalars(
            select(Notification.notification_type)
            .where(Notification.user_id == user.id)
            .order_by(Notification.created_at, Notification.id)
        ).all()
    )


def test_mention_by_email_creates_mention_record(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    mentioned_user = create_member(db_session, account_id=hierarchy["account"]["id"], email="mention@example.com")

    comment = create_comment(client, "TASK", task["id"], "Please review @mention@example.com")

    mentions = client.get(f"/api/v1/comments/{comment['id']}/mentions")
    assert mentions.status_code == 200
    assert [mention["mentioned_user_id"] for mention in mentions.json()] == [str(mentioned_user.id)]


def test_mention_by_full_name_creates_mention_record(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    mentioned_user = create_member(
        db_session,
        account_id=hierarchy["account"]["id"],
        email="jane.member@example.com",
        full_name="Jane Member",
    )

    comment = create_comment(client, "TASK", task["id"], "Can @Jane Member review this?")

    mentions = db_session.scalars(select(CommentMention).where(CommentMention.comment_id == UUID(comment["id"]))).all()
    assert [mention.mentioned_user_id for mention in mentions] == [mentioned_user.id]


def test_unknown_mention_is_ignored(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])

    comment = create_comment(client, "TASK", task["id"], "Unknown @nobody@example.com")

    mentions = db_session.scalars(select(CommentMention).where(CommentMention.comment_id == UUID(comment["id"]))).all()
    assert mentions == []


def test_duplicate_mention_not_created(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    mentioned_user = create_member(
        db_session,
        account_id=hierarchy["account"]["id"],
        email="duplicate@example.com",
        full_name="Duplicate User",
    )

    comment = create_comment(
        client,
        "TASK",
        task["id"],
        "Ping @duplicate@example.com and @duplicate@example.com and @Duplicate User",
    )

    mentions = db_session.scalars(select(CommentMention).where(CommentMention.comment_id == UUID(comment["id"]))).all()
    assert len(mentions) == 1
    assert mentions[0].mentioned_user_id == mentioned_user.id


def test_mention_notification_created(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    mentioned_user = create_member(db_session, account_id=hierarchy["account"]["id"], email="notify@example.com")

    create_comment(client, "TASK", task["id"], "Please review @notify@example.com")

    assert "MENTION" in notification_types(db_session, mentioned_user)


def test_task_assignment_creates_notification(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Assigned Task")
    assigned_user = create_member(db_session, account_id=hierarchy["account"]["id"], email="assignee@example.com")

    response = client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"user_id": str(assigned_user.id)})

    assert response.status_code == 201
    assert notification_types(db_session, assigned_user) == ["TASK_ASSIGNED"]


def test_self_action_does_not_create_notification(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"], "Self Assigned Task")
    dev_user = get_dev_user(db_session)

    response = client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"user_id": str(dev_user.id)})

    assert response.status_code == 201
    assert notification_types(db_session, dev_user) == []


def test_risk_assignment_creates_notification(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    assigned_user = create_member(db_session, account_id=hierarchy["account"]["id"], email="risk-owner@example.com")

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/risks",
        json={"title": "Vendor delay", "assigned_to": str(assigned_user.id)},
    )

    assert response.status_code == 201
    assert notification_types(db_session, assigned_user) == ["RISK_CREATED"]


def test_comment_creates_notification(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    other_member = create_member(db_session, account_id=hierarchy["account"]["id"], email="comment-reader@example.com")

    create_comment(client, "TASK", task["id"], "General update")

    assert notification_types(db_session, other_member) == ["COMMENT_ADDED"]


def test_decision_approved_creates_notification(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    other_member = create_member(db_session, account_id=hierarchy["account"]["id"], email="decision-reader@example.com")
    decision_response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/decisions",
        json={"title": "Approve launch"},
    )
    assert decision_response.status_code == 201
    approved_status_id = get_approved_decision_status_id(db_session, hierarchy["account"]["id"])

    response = client.patch(
        f"/api/v1/decisions/{decision_response.json()['id']}",
        json={"status_id": str(approved_status_id)},
    )

    assert response.status_code == 200
    assert notification_types(db_session, other_member) == ["DECISION_APPROVED"]


def test_read_notification_works(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    assigned_user = create_member(db_session, account_id=hierarchy["account"]["id"], email="read@example.com")
    client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"user_id": str(assigned_user.id)})
    notification = db_session.scalar(select(Notification).where(Notification.user_id == assigned_user.id))
    assert notification is not None

    override_current_user(assigned_user)
    response = client.patch(f"/api/v1/notifications/{notification.id}/read")

    assert response.status_code == 200
    assert response.json()["is_read"] is True


def test_read_all_works(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    assigned_user = create_member(db_session, account_id=hierarchy["account"]["id"], email="read-all@example.com")
    client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"user_id": str(assigned_user.id)})
    client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/risks",
        json={"title": "Assigned risk", "assigned_to": str(assigned_user.id)},
    )

    override_current_user(assigned_user)
    response = client.patch("/api/v1/notifications/read-all")
    unread_response = client.get("/api/v1/notifications", params={"unreadOnly": True})

    assert response.status_code == 200
    assert response.json() == {"updated": 2}
    assert unread_response.status_code == 200
    assert unread_response.json()["results"] == []


def test_user_cannot_read_another_users_notifications(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    assigned_user = create_member(db_session, account_id=hierarchy["account"]["id"], email="private@example.com")
    client.post(f"/api/v1/tasks/{task['id']}/assignments", json={"user_id": str(assigned_user.id)})
    notification = db_session.scalar(select(Notification).where(Notification.user_id == assigned_user.id))
    assert notification is not None

    list_response = client.get("/api/v1/notifications")
    read_response = client.patch(f"/api/v1/notifications/{notification.id}/read")

    assert list_response.status_code == 200
    assert list_response.json()["results"] == []
    assert read_response.status_code == 404
    assert read_response.json()["message"] == "Notification not found."