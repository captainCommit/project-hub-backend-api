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
from app.models.user import User
from app.services.auth import DEV_USER_EMAIL, DEV_USER_FULL_NAME


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


def test_get_me_creates_and_returns_dev_user(client: TestClient, db_session: Session) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == DEV_USER_EMAIL
    assert body["full_name"] == DEV_USER_FULL_NAME
    assert body["is_active"] is True

    user = db_session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    assert user is not None


def test_create_account_creates_owner_membership(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/api/v1/accounts",
        json={"name": "Acme", "slug": "acme"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Acme"
    assert body["slug"] == "acme"

    user = db_session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    assert user is not None

    account_id = UUID(body["id"])
    membership = db_session.scalar(
        select(AccountMember).where(
            AccountMember.account_id == account_id,
            AccountMember.user_id == user.id,
        )
    )
    assert membership is not None
    assert membership.role == AccountMemberRole.OWNER.value


def test_list_accounts_returns_only_current_user_memberships(
    client: TestClient,
    db_session: Session,
) -> None:
    owned_response = client.post(
        "/api/v1/accounts",
        json={"name": "Owned Account", "slug": "owned-account"},
    )
    assert owned_response.status_code == 201

    other_user = User(email="other@example.com", full_name="Other User")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(name="Other Account", slug="other-account", created_by=other_user.id)
    db_session.add(other_account)
    db_session.flush()
    db_session.add(
        AccountMember(
            account_id=other_account.id,
            user_id=other_user.id,
            role=AccountMemberRole.OWNER.value,
        )
    )
    db_session.commit()

    response = client.get("/api/v1/accounts")

    assert response.status_code == 200
    slugs = {account["slug"] for account in response.json()}
    assert slugs == {"owned-account"}


def test_get_account_blocks_non_members(client: TestClient, db_session: Session) -> None:
    other_user = User(email="other@example.com", full_name="Other User")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(name="Other Account", slug="other-account", created_by=other_user.id)
    db_session.add(other_account)
    db_session.flush()
    db_session.add(
        AccountMember(
            account_id=other_account.id,
            user_id=other_user.id,
            role=AccountMemberRole.OWNER.value,
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/accounts/{other_account.id}")

    assert response.status_code == 403
    assert response.json()["message"] == "Account access denied."


def test_patch_account_allows_owner(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "Original", "slug": "original"},
    )
    account_id = UUID(create_response.json()["id"])

    response = client.patch(
        f"/api/v1/accounts/{account_id}",
        json={"name": "Updated", "slug": "updated"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Updated"
    assert body["slug"] == "updated"


def test_patch_account_blocks_insufficient_role(
    client: TestClient,
    db_session: Session,
) -> None:
    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "Viewer Account", "slug": "viewer-account"},
    )
    account_id = UUID(create_response.json()["id"])

    membership = db_session.scalar(
        select(AccountMember).where(AccountMember.account_id == account_id)
    )
    assert membership is not None
    membership.role = AccountMemberRole.VIEWER.value
    db_session.commit()

    response = client.patch(
        f"/api/v1/accounts/{account_id}",
        json={"name": "Blocked Update"},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_get_missing_account_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/accounts/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["message"] == "Account not found."