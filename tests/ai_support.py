"""Fixtures and helpers shared by the AI assistant tests. Import the fixtures into a test module to use them."""

from collections.abc import Generator
from typing import Any
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
from app.models.account_member import AccountMember, AccountMemberRole
from app.models.user import User
from app.routers.ai import get_ai_resolver, get_ai_transport
from app.services.auth import get_current_user
from tests.helpers import grant_access
from tests.test_search import sqlite_similarity

PUBLIC_RESOLVER = lambda host: ["93.184.216.34"]  # noqa: E731


class FakeModel:
    """Stands in for a provider: returns queued OpenAI-format replies and records every request."""

    def __init__(self) -> None:
        self.replies: list[tuple[int, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def reply(self, content: str | None = "", tool_calls: list[dict] | None = None, *, usage: tuple[int, int] = (100, 20)) -> None:
        self.replies.append(
            (
                200,
                {
                    "choices": [{"message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}],
                    "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
                },
            )
        )

    def tool_call(self, name: str, arguments: str = "{}", call_id: str = "call_1", *, usage: tuple[int, int] = (100, 20)) -> None:
        self.reply(None, [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}], usage=usage)

    def fail(self, status: int, message: str) -> None:
        self.replies.append((status, {"error": {"message": message}}))

    def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> tuple[int, Any]:
        self.calls.append({"url": url, "headers": headers, "body": body})
        if not self.replies:
            raise AssertionError("The fake model has no reply queued.")
        return self.replies.pop(0)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # Search uses Postgres's pg_trgm similarity(); give SQLite the same function the search tests use.
    event.listen(engine, "connect", lambda connection, _record: connection.create_function("similarity", 2, sqlite_similarity))
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=Session, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def ai_settings() -> Settings:
    return Settings(auth_mode="local", environment="local", ai_kms_key_id="", ai_local_key_secret="test-secret")


@pytest.fixture()
def fake_model() -> FakeModel:
    return FakeModel()


@pytest.fixture()
def client(db_session: Session, ai_settings: Settings, fake_model: FakeModel) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: ai_settings
    app.dependency_overrides[get_ai_transport] = lambda: fake_model
    app.dependency_overrides[get_ai_resolver] = lambda: PUBLIC_RESOLVER
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def post(client: TestClient, path: str, body: dict) -> dict:
    response = client.post(path, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def create_account(client: TestClient, name: str = "Acme", *, ai: bool = True) -> dict[str, dict]:
    """An account with a portfolio, a program and two projects. ai: turn the AI assistant on, as a platform admin would."""
    suffix = uuid4().hex[:8]
    account = post(client, "/api/v1/accounts", {"name": f"{name} {suffix}", "slug": f"{name.lower()}-{suffix}"})
    if ai:
        response = client.put(f"/api/v1/admin/ai/feature/{account['id']}", json={"enabled": True})
        assert response.status_code == 200, response.text
    portfolio = post(client, f"/api/v1/accounts/{account['id']}/portfolios", {"name": "Portfolio"})
    program = post(client, f"/api/v1/portfolios/{portfolio['id']}/programs", {"name": "Retail"})
    apollo = post(client, f"/api/v1/programs/{program['id']}/projects", {"name": "Apollo"})
    secret = post(client, f"/api/v1/programs/{program['id']}/projects", {"name": "Secret"})
    return {"account": account, "program": program, "apollo": apollo, "secret": secret}


def add_member(
    db_session: Session,
    hierarchy: dict,
    email: str,
    *,
    modules: list[str],
    view_modules: list[str] | None = None,
    role: AccountMemberRole = AccountMemberRole.MEMBER,
) -> User:
    user = User(email=email, full_name=email.split("@")[0].title())
    db_session.add(user)
    db_session.flush()
    db_session.add(AccountMember(account_id=UUID(hierarchy["account"]["id"]), user_id=user.id, role=role.value))
    db_session.commit()
    grant_access(
        db_session,
        account_id=hierarchy["account"]["id"],
        role="VIEWER" if role == AccountMemberRole.VIEWER else "MEMBER",
        project_id=hierarchy["apollo"]["id"],
        modules=modules,
        view_modules=view_modules,
        email=email,
    )
    return user


def dev_user(db_session: Session) -> User:
    user = db_session.scalar(select(User).where(User.email == "dev@example.com"))
    assert user is not None
    return user


class acting_as:
    """Make API calls as another user for the duration of a with-block."""

    def __init__(self, user: User) -> None:
        self.user = user

    def __enter__(self) -> None:
        app.dependency_overrides[get_current_user] = lambda: self.user

    def __exit__(self, *exc: object) -> None:
        app.dependency_overrides.pop(get_current_user, None)
