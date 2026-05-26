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
from app.models.decision import Decision
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
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


RAID_CASES = (
    {
        "entity": "risk",
        "collection": "risks",
        "number_field": "risk_number",
        "prefix": "RISK",
        "create_payload": {"title": "Schedule risk"},
        "patch_payload": {"title": "Updated schedule risk"},
        "patched_field": "title",
        "patched_value": "Updated schedule risk",
        "status_entity_type": "RISK",
        "wrong_option_entity_type": "ISSUE",
        "wrong_option_name": "PRIORITY",
        "wrong_option_field": "priority_id",
        "wrong_option_detail": "Invalid risk priority.",
    },
    {
        "entity": "issue",
        "collection": "issues",
        "number_field": "issue_number",
        "prefix": "ISSUE",
        "create_payload": {"title": "Open issue"},
        "patch_payload": {"title": "Updated open issue"},
        "patched_field": "title",
        "patched_value": "Updated open issue",
        "status_entity_type": "ISSUE",
        "wrong_option_entity_type": "RISK",
        "wrong_option_name": "PRIORITY",
        "wrong_option_field": "priority_id",
        "wrong_option_detail": "Invalid issue priority.",
    },
    {
        "entity": "assumption",
        "collection": "assumptions",
        "number_field": "assumption_number",
        "prefix": "ASS",
        "create_payload": {"description": "Funding is approved"},
        "patch_payload": {"description": "Funding is conditionally approved"},
        "patched_field": "description",
        "patched_value": "Funding is conditionally approved",
        "status_entity_type": "ASSUMPTION",
        "wrong_option_entity_type": "DECISION",
        "wrong_option_name": "STATUS",
        "wrong_option_field": "status_id",
        "wrong_option_detail": "Invalid assumption status.",
    },
    {
        "entity": "decision",
        "collection": "decisions",
        "number_field": "decision_number",
        "prefix": "DEC",
        "create_payload": {"title": "Approve baseline"},
        "patch_payload": {"title": "Approve revised baseline"},
        "patched_field": "title",
        "patched_value": "Approve revised baseline",
        "status_entity_type": "DECISION",
        "wrong_option_entity_type": "ASSUMPTION",
        "wrong_option_name": "STATUS",
        "wrong_option_field": "status_id",
        "wrong_option_detail": "Invalid decision status.",
    },
)

DECISION_CASE = RAID_CASES[3]


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


def set_current_user_role(db_session: Session, account_id: str | UUID, role: AccountMemberRole) -> None:
    membership = db_session.scalar(
        select(AccountMember).where(AccountMember.account_id == UUID(str(account_id)))
    )
    assert membership is not None
    membership.role = role.value
    db_session.commit()


def get_option_id(
    db_session: Session,
    *,
    account_id: str | UUID,
    entity_type: str,
    option_name: str,
    value: str,
) -> UUID:
    option_id = db_session.scalar(
        select(OptionValue.id)
        .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
        .where(
            OptionSet.account_id == UUID(str(account_id)),
            OptionSet.entity_type == entity_type,
            OptionSet.name == option_name,
            OptionValue.value == value,
        )
    )
    assert option_id is not None
    return option_id


def create_raid_item(client: TestClient, project_id: str, case: dict[str, object], **extra: object) -> dict:
    payload = {**case["create_payload"], **extra}  # type: ignore[arg-type]
    response = client.post(f"/api/v1/projects/{project_id}/{case['collection']}", json=payload)
    assert response.status_code == 201
    return response.json()


def create_decision(client: TestClient, project_id: str, **extra: object) -> dict:
    return create_raid_item(client, project_id, DECISION_CASE, **extra)


def create_decision_option(client: TestClient, decision_id: str, **extra: object) -> dict:
    payload = {"title": "Option", **extra}
    response = client.post(f"/api/v1/decisions/{decision_id}/options", json=payload)
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("case", RAID_CASES, ids=[str(case["entity"]) for case in RAID_CASES])
def test_member_can_create_raid_item(client: TestClient, db_session: Session, case: dict[str, object]) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/{case['collection']}",
        json=case["create_payload"],
    )

    assert response.status_code == 201
    item = response.json()
    assert item[case["number_field"]] == f"{case['prefix']}-001"
    assert item["account_id"] == hierarchy["account"]["id"]
    assert item["program_id"] == hierarchy["program"]["id"]


@pytest.mark.parametrize("case", RAID_CASES, ids=[str(case["entity"]) for case in RAID_CASES])
def test_viewer_cannot_create_raid_item(client: TestClient, db_session: Session, case: dict[str, object]) -> None:
    hierarchy = create_work_hierarchy(client)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.post(
        f"/api/v1/projects/{hierarchy['project']['id']}/{case['collection']}",
        json=case["create_payload"],
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient account role."}


@pytest.mark.parametrize("case", RAID_CASES, ids=[str(case["entity"]) for case in RAID_CASES])
def test_non_member_cannot_read_raid_items(client: TestClient, db_session: Session, case: dict[str, object]) -> None:
    other_user = User(email=f"raid-other-{case['entity']}@example.com", full_name="RAID Other")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(
        name=f"Other RAID Account {case['entity']}",
        slug=f"other-raid-account-{case['entity']}",
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
        name="Other Project",
        created_by=other_user.id,
    )
    db_session.add(project)
    db_session.commit()

    response = client.get(f"/api/v1/projects/{project.id}/{case['collection']}")

    assert response.status_code == 403
    assert response.json() == {"detail": "Account access denied."}


@pytest.mark.parametrize("case", RAID_CASES, ids=[str(case["entity"]) for case in RAID_CASES])
def test_raid_numbers_auto_generate_per_project(client: TestClient, case: dict[str, object]) -> None:
    hierarchy = create_work_hierarchy(client)
    other_hierarchy = create_work_hierarchy(client)

    first = create_raid_item(client, hierarchy["project"]["id"], case)
    second = create_raid_item(client, hierarchy["project"]["id"], case)
    other_project_first = create_raid_item(client, other_hierarchy["project"]["id"], case)

    assert first[case["number_field"]] == f"{case['prefix']}-001"
    assert second[case["number_field"]] == f"{case['prefix']}-002"
    assert other_project_first[case["number_field"]] == f"{case['prefix']}-001"


@pytest.mark.parametrize("case", RAID_CASES, ids=[str(case["entity"]) for case in RAID_CASES])
def test_wrong_raid_option_set_is_rejected(
    client: TestClient,
    db_session: Session,
    case: dict[str, object],
) -> None:
    hierarchy = create_work_hierarchy(client)
    wrong_option_id = get_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        entity_type=str(case["wrong_option_entity_type"]),
        option_name=str(case["wrong_option_name"]),
        value="LOW" if case["wrong_option_name"] == "PRIORITY" else "DRAFT",
    )
    payload = {**case["create_payload"], str(case["wrong_option_field"]): str(wrong_option_id)}  # type: ignore[arg-type]

    response = client.post(f"/api/v1/projects/{hierarchy['project']['id']}/{case['collection']}", json=payload)

    assert response.status_code == 400
    assert response.json() == {"detail": case["wrong_option_detail"]}


@pytest.mark.parametrize("case", RAID_CASES, ids=[str(case["entity"]) for case in RAID_CASES])
def test_raid_default_status_is_applied(
    client: TestClient,
    db_session: Session,
    case: dict[str, object],
) -> None:
    hierarchy = create_work_hierarchy(client)
    default_status_id = get_option_id(
        db_session,
        account_id=hierarchy["account"]["id"],
        entity_type=str(case["status_entity_type"]),
        option_name="STATUS",
        value="OPEN" if case["entity"] in {"risk", "issue"} else "DRAFT",
    )

    item = create_raid_item(client, hierarchy["project"]["id"], case)

    assert item["status_id"] == str(default_status_id)
    assert item["status"] is not None
    assert item["status"]["id"] == str(default_status_id)


@pytest.mark.parametrize("case", RAID_CASES, ids=[str(case["entity"]) for case in RAID_CASES])
def test_raid_patch_works_for_allowed_role(
    client: TestClient,
    db_session: Session,
    case: dict[str, object],
) -> None:
    hierarchy = create_work_hierarchy(client)
    item = create_raid_item(client, hierarchy["project"]["id"], case)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MANAGER)

    response = client.patch(f"/api/v1/{case['collection']}/{item['id']}", json=case["patch_payload"])

    assert response.status_code == 200
    assert response.json()[case["patched_field"]] == case["patched_value"]


@pytest.mark.parametrize("case", RAID_CASES, ids=[str(case["entity"]) for case in RAID_CASES])
def test_raid_delete_returns_501(client: TestClient, case: dict[str, object]) -> None:
    hierarchy = create_work_hierarchy(client)
    item = create_raid_item(client, hierarchy["project"]["id"], case)

    response = client.delete(f"/api/v1/{case['collection']}/{item['id']}")

    assert response.status_code == 501
    assert response.json() == {"detail": f"{str(case['entity']).capitalize()} deletion is not implemented in Phase 4A."}


def test_member_can_create_decision_option(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    decision = create_decision(client, hierarchy["project"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    response = client.post(
        f"/api/v1/decisions/{decision['id']}/options",
        json={"title": "Build", "pros": "Fast", "cons": "Cost", "work_effort": "Medium", "sort_order": 2},
    )

    assert response.status_code == 201
    option = response.json()
    assert option["account_id"] == hierarchy["account"]["id"]
    assert option["decision_id"] == decision["id"]
    assert option["title"] == "Build"
    assert option["pros"] == "Fast"
    assert option["cons"] == "Cost"
    assert option["work_effort"] == "Medium"
    assert option["sort_order"] == 2


def test_viewer_cannot_create_decision_option(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    decision = create_decision(client, hierarchy["project"]["id"])
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.VIEWER)

    response = client.post(
        f"/api/v1/decisions/{decision['id']}/options",
        json={"title": "Blocked"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient account role."}


def test_non_member_cannot_read_decision_options(client: TestClient, db_session: Session) -> None:
    other_user = User(email="decision-option-other@example.com", full_name="Decision Option Other")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(
        name="Other Decision Option Account",
        slug="other-decision-option-account",
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
        name="Other Project",
        created_by=other_user.id,
    )
    db_session.add(project)
    db_session.flush()
    decision = Decision(
        account_id=other_account.id,
        project_id=project.id,
        program_id=program.id,
        decision_number="DEC-001",
        title="Other Decision",
        created_by=other_user.id,
    )
    db_session.add(decision)
    db_session.commit()

    response = client.get(f"/api/v1/decisions/{decision.id}/options")

    assert response.status_code == 403
    assert response.json() == {"detail": "Account access denied."}


def test_decision_options_list_ordered_by_sort_order(client: TestClient) -> None:
    hierarchy = create_work_hierarchy(client)
    decision = create_decision(client, hierarchy["project"]["id"])
    create_decision_option(client, decision["id"], title="Third", sort_order=30)
    create_decision_option(client, decision["id"], title="First", sort_order=10)
    create_decision_option(client, decision["id"], title="Second", sort_order=20)

    response = client.get(f"/api/v1/decisions/{decision['id']}/options")

    assert response.status_code == 200
    assert [option["title"] for option in response.json()] == ["First", "Second", "Third"]


def test_decision_option_patch_works(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    decision = create_decision(client, hierarchy["project"]["id"])
    option = create_decision_option(client, decision["id"], title="Original", sort_order=1)
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MANAGER)

    response = client.patch(
        f"/api/v1/decision-options/{option['id']}",
        json={"title": "Updated", "pros": "Better", "sort_order": 5},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["title"] == "Updated"
    assert updated["pros"] == "Better"
    assert updated["sort_order"] == 5


def test_decision_option_delete_works(client: TestClient, db_session: Session) -> None:
    hierarchy = create_work_hierarchy(client)
    decision = create_decision(client, hierarchy["project"]["id"])
    option = create_decision_option(client, decision["id"], title="Delete me")
    set_current_user_role(db_session, hierarchy["account"]["id"], AccountMemberRole.MEMBER)

    delete_response = client.delete(f"/api/v1/decision-options/{option['id']}")
    get_response = client.get(f"/api/v1/decision-options/{option['id']}")

    assert delete_response.status_code == 204
    assert get_response.status_code == 404
    assert get_response.json() == {"detail": "Decision option not found."}