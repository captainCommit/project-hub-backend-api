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
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.portfolio import Portfolio
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


def create_portfolio(client: TestClient, account_id: str, name: str = "Portfolio A") -> dict:
    response = client.post(f"/api/v1/accounts/{account_id}/portfolios", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_program(client: TestClient, portfolio_id: str, name: str = "Program A") -> dict:
    response = client.post(f"/api/v1/portfolios/{portfolio_id}/programs", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_project(client: TestClient, program_id: str, name: str = "Project A") -> dict:
    response = client.post(f"/api/v1/programs/{program_id}/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()


def set_current_user_role(db_session: Session, account_id: str | UUID, role: AccountMemberRole) -> None:
    membership = db_session.scalar(
        select(AccountMember).where(AccountMember.account_id == UUID(str(account_id)))
    )
    assert membership is not None
    membership.role = role.value
    db_session.commit()


def get_status_id(
    db_session: Session,
    *,
    account_id: str | UUID,
    entity_type: str,
    name: str = "STATUS",
    value: str = "ACTIVE",
) -> UUID:
    status_id = db_session.scalar(
        select(OptionValue.id)
        .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
        .where(
            OptionSet.account_id == UUID(str(account_id)),
            OptionSet.entity_type == entity_type,
            OptionSet.name == name,
            OptionValue.value == value,
        )
    )
    assert status_id is not None
    return status_id


def test_owner_can_create_portfolio(client: TestClient) -> None:
    account = create_account(client)

    response = client.post(
        f"/api/v1/accounts/{account['id']}/portfolios",
        json={"name": "Strategic Portfolio", "color": "#3366ff"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["account_id"] == account["id"]
    assert body["name"] == "Strategic Portfolio"
    assert body["color"] == "#3366ff"


def test_viewer_cannot_create_portfolio(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    set_current_user_role(db_session, account["id"], AccountMemberRole.VIEWER)

    response = client.post(
        f"/api/v1/accounts/{account['id']}/portfolios",
        json={"name": "Blocked Portfolio"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient account role."}


def test_portfolio_creation_uses_default_portfolio_status(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_account(client)
    active_status_id = get_status_id(db_session, account_id=account["id"], entity_type="PORTFOLIO")

    portfolio = create_portfolio(client, account["id"])

    assert portfolio["status_id"] == str(active_status_id)


def test_invalid_status_id_from_wrong_entity_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_account(client)
    task_status_id = get_status_id(
        db_session,
        account_id=account["id"],
        entity_type="TASK",
        value="NOT_STARTED",
    )

    response = client.post(
        f"/api/v1/accounts/{account['id']}/portfolios",
        json={"name": "Bad Status Portfolio", "status_id": str(task_status_id)},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid portfolio status."}


def test_program_creation_requires_valid_portfolio(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/portfolios/{UUID(int=0)}/programs",
        json={"name": "Program Without Portfolio"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Portfolio not found."}


def test_project_creation_requires_valid_program_and_portfolio_consistency(
    client: TestClient,
) -> None:
    account = create_account(client)
    portfolio_a = create_portfolio(client, account["id"], "Portfolio A")
    portfolio_b = create_portfolio(client, account["id"], "Portfolio B")
    program = create_program(client, portfolio_a["id"])

    response = client.post(
        f"/api/v1/programs/{program['id']}/projects",
        json={"name": "Invalid Project", "portfolio_id": portfolio_b["id"]},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Program does not belong to the supplied portfolio."}


def test_account_member_can_read_portfolio_program_project(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_account(client)
    portfolio = create_portfolio(client, account["id"])
    program = create_program(client, portfolio["id"])
    project = create_project(client, program["id"])
    set_current_user_role(db_session, account["id"], AccountMemberRole.VIEWER)

    portfolio_response = client.get(f"/api/v1/portfolios/{portfolio['id']}")
    program_response = client.get(f"/api/v1/programs/{program['id']}")
    project_response = client.get(f"/api/v1/projects/{project['id']}")

    assert portfolio_response.status_code == 200
    assert program_response.status_code == 200
    assert project_response.status_code == 200


def test_non_member_is_blocked_from_reading_portfolio(
    client: TestClient,
    db_session: Session,
) -> None:
    other_user = User(email="hierarchy-other@example.com", full_name="Hierarchy Other")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(name="Other Account", slug="hierarchy-other", created_by=other_user.id)
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
    db_session.commit()

    response = client.get(f"/api/v1/portfolios/{portfolio.id}")

    assert response.status_code == 403
    assert response.json() == {"detail": "Account access denied."}


def test_sidebar_returns_nested_hierarchy(client: TestClient) -> None:
    account = create_account(client)
    portfolio = create_portfolio(client, account["id"], "Portfolio A")
    program = create_program(client, portfolio["id"], "Program A")
    project = create_project(client, program["id"], "Project A")

    response = client.get(f"/api/v1/accounts/{account['id']}/sidebar")

    assert response.status_code == 200
    body = response.json()
    assert body["portfolios"][0]["id"] == portfolio["id"]
    assert body["portfolios"][0]["status"]["value"] == "ACTIVE"
    assert body["portfolios"][0]["programs"][0]["id"] == program["id"]
    assert body["portfolios"][0]["programs"][0]["projects"][0]["id"] == project["id"]


def test_overview_endpoints_return_counts(client: TestClient) -> None:
    account = create_account(client)
    portfolio = create_portfolio(client, account["id"])
    program = create_program(client, portfolio["id"])
    create_project(client, program["id"], "Project A")
    create_project(client, program["id"], "Project B")

    portfolio_response = client.get(f"/api/v1/portfolios/{portfolio['id']}/overview")
    program_response = client.get(f"/api/v1/programs/{program['id']}/overview")

    assert portfolio_response.status_code == 200
    assert portfolio_response.json()["program_count"] == 1
    assert portfolio_response.json()["project_count"] == 2
    assert program_response.status_code == 200
    assert program_response.json()["project_count"] == 2