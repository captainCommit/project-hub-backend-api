from collections.abc import Generator
from datetime import UTC, datetime
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
from app.models.account import Account
from app.models.account_member import AccountMember, AccountMemberRole
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.task import Task
from app.models.user import User


def trigram_tokens(value: object) -> set[str]:
    normalized = " ".join(str(value).lower().split())
    if not normalized:
        return set()
    padded = f"  {normalized} "
    return {padded[index : index + 3] for index in range(len(padded) - 2)}


def sqlite_similarity(left: object, right: object) -> float:
    if left is None or right is None:
        return 0.0
    left_value = str(left).lower()
    right_value = str(right).lower()
    if left_value == right_value:
        return 1.0
    left_trigrams = trigram_tokens(left_value)
    right_trigrams = trigram_tokens(right_value)
    if not left_trigrams or not right_trigrams:
        return 0.0
    return len(left_trigrams & right_trigrams) / len(left_trigrams | right_trigrams)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_similarity(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.create_function("similarity", 2, sqlite_similarity)

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


def create_work_hierarchy(
    client: TestClient,
    *,
    portfolio_name: str = "Portfolio A",
    program_name: str = "Program A",
    project_name: str = "Project A",
) -> dict[str, dict]:
    unique_suffix = uuid4().hex[:8]
    account = create_account(client, name=f"Acme {unique_suffix}", slug=f"acme-{unique_suffix}")
    portfolio_response = client.post(
        f"/api/v1/accounts/{account['id']}/portfolios",
        json={"name": portfolio_name},
    )
    assert portfolio_response.status_code == 201
    portfolio = portfolio_response.json()
    program_response = client.post(
        f"/api/v1/portfolios/{portfolio['id']}/programs",
        json={"name": program_name},
    )
    assert program_response.status_code == 201
    program = program_response.json()
    project_response = client.post(
        f"/api/v1/programs/{program['id']}/projects",
        json={"name": project_name},
    )
    assert project_response.status_code == 201
    return {"account": account, "portfolio": portfolio, "program": program, "project": project_response.json()}


def create_task(client: TestClient, project_id: str, name: str = "Task", **extra: object) -> dict:
    payload = {"name": name, **extra}
    response = client.post(f"/api/v1/projects/{project_id}/tasks", json=payload)
    assert response.status_code == 201
    return response.json()


def create_risk(client: TestClient, project_id: str, title: str = "Risk", **extra: object) -> dict:
    payload = {"title": title, **extra}
    response = client.post(f"/api/v1/projects/{project_id}/risks", json=payload)
    assert response.status_code == 201
    return response.json()


def set_current_user_role(db_session: Session, account_id: str | UUID, role: AccountMemberRole) -> None:
    membership = db_session.scalar(
        select(AccountMember).where(AccountMember.account_id == UUID(str(account_id)))
    )
    assert membership is not None
    membership.role = role.value
    db_session.commit()


def test_search_project_by_exact_name(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client, program_name="Program A", project_name="Website Redesign")

    response = client.get("/api/v1/search", params={"q": "Website Redesign", "entity_types": "PROJECT"})

    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0] == {
        "entity_type": "PROJECT",
        "id": hierarchy["project"]["id"],
        "title": "Website Redesign",
        "subtitle": "Program A",
        "score": 1.0,
    }


def test_search_task_by_partial_name(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client, project_name="Delivery Project")
    task = create_task(client, hierarchy["project"]["id"], "Build navigation menu")

    response = client.get("/api/v1/search", params={"q": "navigation", "entity_types": "TASK"})

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["entity_type"] == "TASK"
    assert results[0]["id"] == task["id"]
    assert results[0]["title"] == "Build navigation menu"
    assert results[0]["subtitle"] == "Delivery Project"


def test_search_risk_by_similarity(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client, project_name="Migration Project")
    risk = create_risk(client, hierarchy["project"]["id"], "Schedule compression risk")

    response = client.get(
        "/api/v1/search",
        params={"q": "Schedule comprssion risk", "entity_types": "RISK"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["entity_type"] == "RISK"
    assert results[0]["id"] == risk["id"]
    assert results[0]["title"] == "Schedule compression risk"
    assert results[0]["score"] > 0.1


def test_search_entity_type_filtering(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client, project_name="Shared Alpha")
    task = create_task(client, hierarchy["project"]["id"], "Shared Alpha launch task")

    response = client.get("/api/v1/search", params={"q": "Shared Alpha", "entity_types": "TASK"})

    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "entity_type": "TASK",
            "id": task["id"],
            "title": "Shared Alpha launch task",
            "subtitle": "Shared Alpha",
            "score": pytest.approx(sqlite_similarity("Shared Alpha launch task", "Shared Alpha")),
        }
    ]


def test_search_result_ranking_uses_exact_score_then_created_at(
    client: TestClient,
    db_session: Session,
) -> None:
    hierarchy = create_work_hierarchy(client, project_name="Ranking Project")
    similar_task = create_task(client, hierarchy["project"]["id"], "Deploy release candidate")
    older_exact_task = create_task(client, hierarchy["project"]["id"], "Deploy release")
    newer_exact_task = create_task(client, hierarchy["project"]["id"], "Deploy release")

    db_session.get(Task, UUID(older_exact_task["id"])).created_at = datetime(2026, 1, 1, tzinfo=UTC)
    db_session.get(Task, UUID(newer_exact_task["id"])).created_at = datetime(2026, 1, 2, tzinfo=UTC)
    db_session.get(Task, UUID(similar_task["id"])).created_at = datetime(2026, 1, 3, tzinfo=UTC)
    db_session.commit()

    response = client.get("/api/v1/search", params={"q": "Deploy release", "entity_types": "TASK"})

    assert response.status_code == 200
    results = response.json()["results"]
    assert [result["id"] for result in results[:3]] == [
        newer_exact_task["id"],
        older_exact_task["id"],
        similar_task["id"],
    ]


def test_viewer_can_search(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client, project_name="Viewer Visible Project")
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.get("/api/v1/search", params={"q": "Viewer Visible Project", "entity_types": "PROJECT"})

    assert response.status_code == 200
    assert response.json()["results"][0]["id"] == hierarchy["project"]["id"]


def test_non_member_gets_no_results(client: TestClient, db_session: Session) -> None:
    other_user = User(email="search-other@example.com", full_name="Search Other")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(
        name="Other Search Account",
        slug="other-search-account",
        created_by=other_user.id,
    )
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
        name="Private Search Project",
        created_by=other_user.id,
    )
    db_session.add(project)
    db_session.commit()

    response = client.get("/api/v1/search", params={"q": "Private Search Project"})

    assert response.status_code == 200
    assert response.json() == {"results": []}