from collections.abc import Generator
from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.activity_log import ActivityLog
from app.models.comment import Comment
from app.models.notification import Notification, NotificationType
from app.models.project import Project
from app.models.risk import Risk
from app.models.task import Task
from tests.test_account_dashboard import create_program, create_project, create_raid_item, create_task, create_work_hierarchy


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    # Postgres removes child rows with ON DELETE CASCADE; SQLite only does once foreign keys are switched on.
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=Session, expire_on_commit=False)()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: Settings(auth_mode="local")
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def count(db: Session, model, *conditions) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conditions))


def test_deleting_a_project_removes_its_work_comments_and_notifications(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    project_id = hierarchy["project"]["id"]
    me = client.get("/api/v1/me").json()
    task = create_task(client, project_id, "Build", start_date=date.today(), finish_date=date.today())
    create_raid_item(client, project_id, "risks", title="Slip")
    comment = client.post(f"/api/v1/entities/TASK/{task['id']}/comments", json={"body": f"@{me['full_name']} look"})
    assert comment.status_code == 201
    db_session.add(Notification(
        account_id=UUID(hierarchy["account"]["id"]), user_id=UUID(me["id"]), entity_type="TASK", entity_id=UUID(task["id"]),
        notification_type=NotificationType.TASK_ASSIGNED.value, title="Assigned",
    ))
    db_session.commit()

    response = client.delete(f"/api/v1/projects/{project_id}")

    assert response.status_code == 204
    assert count(db_session, Project) == 0
    assert count(db_session, Task) == 0
    assert count(db_session, Risk) == 0
    assert count(db_session, Comment) == 0
    assert count(db_session, Notification) == 0
    assert count(db_session, ActivityLog, ActivityLog.action == "DELETED", ActivityLog.entity_type == "PROJECT") == 1
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 404


def test_deleting_a_program_or_portfolio_removes_everything_under_it(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    portfolio_id = hierarchy["portfolio"]["id"]
    second_program = create_program(client, portfolio_id, "Second")
    kept = create_project(client, second_program["id"], "Kept")
    create_task(client, hierarchy["project"]["id"], "Goes")

    assert client.delete(f"/api/v1/programs/{hierarchy['program']['id']}").status_code == 204
    assert [p.id for p in db_session.scalars(select(Project)).all()] == [UUID(kept["id"])]
    assert count(db_session, Task) == 0

    assert client.delete(f"/api/v1/portfolios/{portfolio_id}").status_code == 204
    assert count(db_session, Project) == 0
    assert client.get(f"/api/v1/portfolios/{portfolio_id}").status_code == 404


def test_deleting_an_unknown_project_is_404(client: TestClient) -> None:
    create_work_hierarchy(client)
    assert client.delete("/api/v1/projects/00000000-0000-0000-0000-000000000000").status_code == 404
