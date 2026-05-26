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
from app.models.attachment import Attachment
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.services import attachments as attachment_service_module


class FakeS3Client:
    def __init__(self) -> None:
        self.presigned_calls: list[dict[str, object]] = []
        self.deleted_objects: list[dict[str, str]] = []

    def generate_presigned_url(self, client_method: str, **kwargs: object) -> str:
        self.presigned_calls.append({"client_method": client_method, **kwargs})
        return f"https://s3.example.com/{client_method}"

    def delete_object(self, **kwargs: str) -> None:
        self.deleted_objects.append(kwargs)


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
def fake_s3(monkeypatch: pytest.MonkeyPatch) -> FakeS3Client:
    client = FakeS3Client()
    monkeypatch.setattr(attachment_service_module, "get_s3_client", lambda settings: client)
    return client


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def override_get_settings() -> Settings:
        return Settings(auth_mode="local", s3_bucket_name="attachments-test-bucket")

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


def create_upload(client: TestClient, task_id: str, file_name: str = "Example File.pdf") -> dict:
    response = client.post(
        f"/api/v1/entities/TASK/{task_id}/attachments/presigned-upload",
        json={"file_name": file_name, "content_type": "application/pdf", "size_bytes": 12345},
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


def test_member_can_request_upload_url_for_task(
    client: TestClient,
    db_session: Session,
    fake_s3: FakeS3Client,
) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.post(
        f"/api/v1/entities/TASK/{task['id']}/attachments/presigned-upload",
        json={"file_name": "Example File.pdf", "content_type": "application/pdf", "size_bytes": 12345},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["upload_url"] == "https://s3.example.com/put_object"
    assert body["method"] == "PUT"
    assert body["headers"] == {"Content-Type": "application/pdf"}
    assert body["s3_key"].startswith(f"accounts/{hierarchy['account']['id']}/TASK/{task['id']}/")
    assert body["s3_key"].endswith("/Example_File.pdf")
    assert fake_s3.presigned_calls[0]["client_method"] == "put_object"


def test_viewer_cannot_request_upload_url(
    client: TestClient,
    db_session: Session,
    fake_s3: FakeS3Client,
) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.post(
        f"/api/v1/entities/TASK/{task['id']}/attachments/presigned-upload",
        json={"file_name": "Blocked.pdf", "content_type": "application/pdf", "size_bytes": 1},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient account role."}
    assert fake_s3.presigned_calls == []


def test_viewer_can_list_attachments(client: TestClient, db_session: Session, fake_s3: FakeS3Client) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    upload = create_upload(client, task["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.get(f"/api/v1/entities/TASK/{task['id']}/attachments")

    assert response.status_code == 200
    assert response.json()[0]["id"] == upload["attachment_id"]


def test_non_member_cannot_list_attachments(client: TestClient, db_session: Session) -> None:
    other_user = User(email="attachment-other@example.com", full_name="Attachment Other")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(name="Other Attachment Account", slug="other-attachment-account", created_by=other_user.id)
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

    response = client.get(f"/api/v1/entities/TASK/{task.id}/attachments")

    assert response.status_code == 403
    assert response.json() == {"detail": "Account access denied."}


def test_presigned_download_url_works_for_member(
    client: TestClient,
    fake_s3: FakeS3Client,
) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    upload = create_upload(client, task["id"])

    response = client.get(f"/api/v1/attachments/{upload['attachment_id']}/presigned-download")

    assert response.status_code == 200
    assert response.json() == {"download_url": "https://s3.example.com/get_object"}
    assert fake_s3.presigned_calls[-1]["client_method"] == "get_object"


def test_delete_attachment_removes_db_record(
    client: TestClient,
    db_session: Session,
    fake_s3: FakeS3Client,
) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    upload = create_upload(client, task["id"])
    attachment_id = UUID(upload["attachment_id"])

    response = client.delete(f"/api/v1/attachments/{attachment_id}")

    assert response.status_code == 204
    assert db_session.get(Attachment, attachment_id) is None
    assert fake_s3.deleted_objects[0]["Bucket"] == "attachments-test-bucket"


def test_activity_log_created_for_attachment_add_and_delete(
    client: TestClient,
    fake_s3: FakeS3Client,
) -> None:
    hierarchy = create_work_hierarchy(client)
    task = create_task(client, hierarchy["project"]["id"])
    upload = create_upload(client, task["id"])

    add_activity_response = client.get(f"/api/v1/entities/TASK/{task['id']}/activity")
    delete_response = client.delete(f"/api/v1/attachments/{upload['attachment_id']}")
    remove_activity_response = client.get(f"/api/v1/entities/TASK/{task['id']}/activity")

    assert add_activity_response.status_code == 200
    assert add_activity_response.json()[0]["action"] == "ATTACHMENT_ADDED"
    assert delete_response.status_code == 204
    assert remove_activity_response.status_code == 200
    assert remove_activity_response.json()[0]["action"] == "ATTACHMENT_REMOVED"