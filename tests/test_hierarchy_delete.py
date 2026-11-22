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
from app.models.resource import Resource
from tests.test_account_dashboard import (
    create_allocation, create_program, create_project, create_raid_item, create_resource, create_task, create_work_hierarchy,
)


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

    assert response.status_code == 200
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

    assert client.delete(f"/api/v1/programs/{hierarchy['program']['id']}").status_code == 200
    assert [p.id for p in db_session.scalars(select(Project)).all()] == [UUID(kept["id"])]
    assert count(db_session, Task) == 0

    assert client.delete(f"/api/v1/portfolios/{portfolio_id}").status_code == 200
    assert count(db_session, Project) == 0
    assert client.get(f"/api/v1/portfolios/{portfolio_id}").status_code == 404


def test_deleting_an_unknown_project_is_404(client: TestClient) -> None:
    create_work_hierarchy(client)
    assert client.delete("/api/v1/projects/00000000-0000-0000-0000-000000000000").status_code == 404


# Recently deleted


def test_a_deleted_project_comes_back_with_its_work_comments_and_links(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    account_id = hierarchy["account"]["id"]
    project_id = hierarchy["project"]["id"]
    parent = create_task(client, project_id, "Phase", start_date=date.today(), finish_date=date.today())
    child = create_task(client, project_id, "Build", parent_task_id=parent["id"], start_date=date.today(), finish_date=date.today())
    risk = create_raid_item(client, project_id, "risks", title="Slip")
    resource = create_resource(client, account_id, "Ana")
    create_allocation(client, child["id"], resource["id"])
    assert client.post(f"/api/v1/entities/TASK/{child['id']}/comments", json={"body": "Looks good"}).status_code == 201

    deleted = client.delete(f"/api/v1/projects/{project_id}").json()
    assert deleted["entity_type"] == "PROJECT" and deleted["name"] == hierarchy["project"]["name"]
    assert deleted["location"] == f"{hierarchy['portfolio']['name']} / {hierarchy['program']['name']}"
    listed = client.get(f"/api/v1/accounts/{account_id}/recently-deleted").json()
    assert [item["id"] for item in listed] == [deleted["id"]]
    assert count(db_session, Task) == 0

    restored = client.post(f"/api/v1/recently-deleted/{deleted['id']}/restore")

    assert restored.status_code == 200, restored.text
    assert restored.json()["entity_id"] == project_id
    db_session.expire_all()
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 200
    tasks = {t.name: t for t in db_session.scalars(select(Task)).all()}
    assert str(tasks["Build"].parent_task_id) == parent["id"]
    assert count(db_session, Risk) == 1 and str(db_session.scalars(select(Risk.id)).one()) == risk["id"]
    assert count(db_session, Comment) == 1
    assert client.get(f"/api/v1/tasks/{child['id']}/resource-allocations").json()
    assert count(db_session, ActivityLog, ActivityLog.action == "RESTORED") == 1
    assert client.get(f"/api/v1/accounts/{account_id}/recently-deleted").json() == []


def test_a_deleted_portfolio_comes_back_with_its_programs_and_projects(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    portfolio_id = hierarchy["portfolio"]["id"]
    create_task(client, hierarchy["project"]["id"], "Build")

    deleted = client.delete(f"/api/v1/portfolios/{portfolio_id}").json()
    assert deleted["project_count"] == 1
    assert client.post(f"/api/v1/recently-deleted/{deleted['id']}/restore").status_code == 200

    db_session.expire_all()
    assert client.get(f"/api/v1/programs/{hierarchy['program']['id']}").status_code == 200
    assert client.get(f"/api/v1/projects/{hierarchy['project']['id']}").status_code == 200
    assert count(db_session, Task) == 1


def test_a_project_waits_for_its_deleted_program(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    project = client.delete(f"/api/v1/projects/{hierarchy['project']['id']}").json()
    program = client.delete(f"/api/v1/programs/{hierarchy['program']['id']}").json()

    blocked = client.post(f"/api/v1/recently-deleted/{project['id']}/restore")
    assert blocked.status_code == 409
    assert "Restore the program first" in blocked.text

    assert client.post(f"/api/v1/recently-deleted/{program['id']}/restore").status_code == 200
    assert client.post(f"/api/v1/recently-deleted/{project['id']}/restore").status_code == 200
    assert client.get(f"/api/v1/projects/{hierarchy['project']['id']}").status_code == 200


def test_permanently_deleting_removes_it_from_recently_deleted(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    deleted = client.delete(f"/api/v1/projects/{hierarchy['project']['id']}").json()

    assert client.delete(f"/api/v1/recently-deleted/{deleted['id']}").status_code == 204
    assert client.get(f"/api/v1/accounts/{hierarchy['account']['id']}/recently-deleted").json() == []
    assert client.post(f"/api/v1/recently-deleted/{deleted['id']}/restore").status_code == 404
