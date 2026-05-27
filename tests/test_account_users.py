from collections.abc import Generator
from uuid import UUID

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


def add_account_user(
    db_session: Session,
    *,
    account_id: str | UUID,
    email: str,
    full_name: str,
    role: AccountMemberRole = AccountMemberRole.MEMBER,
    cognito_sub: str | None = None,
) -> User:
    user = User(email=email, full_name=full_name, cognito_sub=cognito_sub)
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


def create_private_account(db_session: Session) -> Account:
    other_user = User(email="private-owner@example.com", full_name="Private Owner")
    db_session.add(other_user)
    db_session.flush()
    account = Account(name="Private Account", slug="private-account", created_by=other_user.id)
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


def test_account_member_can_list_account_users(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    add_account_user(db_session, account_id=account["id"], email="alice@example.com", full_name="Alice Example")

    response = client.get(f"/api/v1/accounts/{account['id']}/users")

    assert response.status_code == 200
    emails = {user["email"] for user in response.json()}
    assert emails == {"dev@example.com", "alice@example.com"}


def test_non_member_cannot_list_account_users(client: TestClient, db_session: Session) -> None:
    account = create_private_account(db_session)

    response = client.get(f"/api/v1/accounts/{account.id}/users")

    assert response.status_code == 403
    assert response.json()["message"] == "Account access denied."


def test_search_account_users_by_email(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    add_account_user(db_session, account_id=account["id"], email="jane.special@example.com", full_name="Jane Doe")

    response = client.get(f"/api/v1/accounts/{account['id']}/users/search", params={"q": "special"})

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["email"] == "jane.special@example.com"


def test_search_account_users_by_full_name(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    add_account_user(db_session, account_id=account["id"], email="alex@example.com", full_name="Alex Capacity")

    response = client.get(f"/api/v1/accounts/{account['id']}/users/search", params={"q": "Capacity"})

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["full_name"] == "Alex Capacity"


def test_account_users_do_not_expose_cognito_sub(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    add_account_user(
        db_session,
        account_id=account["id"],
        email="cognito-person@example.com",
        full_name="Cognito Person",
        cognito_sub="secret-sub",
    )

    response = client.get(f"/api/v1/accounts/{account['id']}/users/search", params={"q": "cognito-person"})

    assert response.status_code == 200
    result = response.json()[0]
    assert result["email"] == "cognito-person@example.com"
    assert "cognito_sub" not in result


def test_account_users_pagination_works(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    add_account_user(db_session, account_id=account["id"], email="page-a@example.com", full_name="Page A")
    add_account_user(db_session, account_id=account["id"], email="page-b@example.com", full_name="Page B")

    response = client.get(
        f"/api/v1/accounts/{account['id']}/users",
        params={"paginated": "true", "page": 1, "page_size": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total"] == 3
    assert len(body["items"]) == 2