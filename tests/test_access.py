"""Project-level access: grants, module toggles, seat caps and who may manage them."""

from collections.abc import Callable, Generator
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
from app.models.access_grant import AccessGrant, ProjectModule
from app.models.account_member import AccountMember
from app.models.user import User
from app.services.auth import get_current_user


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: Settings(auth_mode="local")
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def act_as() -> Generator[Callable[[User | None], None], None, None]:
    """Switch the signed-in user; None goes back to the local dev user (the account owner)."""

    def switch(user: User | None) -> None:
        if user is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = lambda: user

    yield switch
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def world(client: TestClient) -> dict[str, dict]:
    """An account owned by the dev user: one portfolio, two programs, a project in each."""
    account = client.post("/api/v1/accounts", json={"name": "Acme", "slug": "acme"}).json()
    portfolio = client.post(f"/api/v1/accounts/{account['id']}/portfolios", json={"name": "Portfolio"}).json()
    program = client.post(f"/api/v1/portfolios/{portfolio['id']}/programs", json={"name": "Program A"}).json()
    other_program = client.post(f"/api/v1/portfolios/{portfolio['id']}/programs", json={"name": "Program B"}).json()
    project = client.post(f"/api/v1/programs/{program['id']}/projects", json={"name": "Project A"}).json()
    other_project = client.post(f"/api/v1/programs/{other_program['id']}/projects", json={"name": "Project B"}).json()
    return {
        "account": account,
        "portfolio": portfolio,
        "program": program,
        "other_program": other_program,
        "project": project,
        "other_project": other_project,
    }


def add_member(db_session: Session, account_id: str, email: str, role: str) -> User:
    user = User(email=email, full_name=email.split("@")[0], cognito_sub=f"sub-{email}")
    db_session.add(user)
    db_session.flush()
    db_session.add(AccountMember(account_id=UUID(account_id), user_id=user.id, role=role))
    db_session.commit()
    return user


def grant(client: TestClient, account_id: str, user: User, role: str, **scope_and_modules: object):
    return client.post(
        f"/api/v1/accounts/{account_id}/access-grants",
        json={"user_id": str(user.id), "role": role, **scope_and_modules},
    )


def test_member_edits_only_the_modules_they_were_given(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    member = add_member(db_session, world["account"]["id"], "m@acme.example", "MEMBER")
    response = grant(client, world["account"]["id"], member, "MEMBER", project_id=world["project"]["id"], modules=["TASKS"])
    assert response.status_code == 201
    risk = client.post(f"/api/v1/projects/{world['project']['id']}/risks", json={"title": "Owner risk"}).json()

    act_as(member)
    project_id = world["project"]["id"]
    assert client.post(f"/api/v1/projects/{project_id}/tasks", json={"name": "Mine"}).status_code == 201
    assert client.get(f"/api/v1/projects/{project_id}/risks").status_code == 200
    created_risk = client.post(f"/api/v1/projects/{project_id}/risks", json={"title": "Nope"})
    assert created_risk.status_code == 403
    assert created_risk.json()["message"] == "Insufficient project permission."
    assert client.patch(f"/api/v1/risks/{risk['id']}", json={"title": "Edited"}).status_code == 403
    # Comments follow the module of what they are on.
    assert client.post(f"/api/v1/entities/RISK/{risk['id']}/comments", json={"body": "hi"}).status_code == 403
    assert client.get(f"/api/v1/projects/{world['other_project']['id']}").status_code == 403


def test_grants_are_capped_by_the_seat(client: TestClient, db_session: Session, world: dict) -> None:
    viewer = add_member(db_session, world["account"]["id"], "v@acme.example", "VIEWER")
    owner = db_session.scalar(select(User).where(User.email == "dev@example.com"))

    too_high = grant(client, world["account"]["id"], viewer, "MEMBER", project_id=world["project"]["id"], modules=["TASKS"])
    for_owner = grant(client, world["account"]["id"], owner, "VIEWER", project_id=world["project"]["id"])

    assert too_high.status_code == 400
    assert too_high.json()["message"] == "A viewer seat can't be given the member role. Change their account role first."
    assert for_owner.status_code == 400
    assert grant(client, world["account"]["id"], viewer, "VIEWER", project_id=world["project"]["id"]).status_code == 201


def test_managers_grant_within_what_they_manage(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    manager = add_member(db_session, world["account"]["id"], "boss@acme.example", "MANAGER")
    member = add_member(db_session, world["account"]["id"], "m@acme.example", "MEMBER")
    grant(client, world["account"]["id"], manager, "MANAGER", program_id=world["program"]["id"])

    act_as(manager)
    inside = grant(client, world["account"]["id"], member, "MEMBER", project_id=world["project"]["id"], modules=["RISKS"])
    outside = grant(client, world["account"]["id"], member, "VIEWER", project_id=world["other_project"]["id"])
    portfolio_wide = grant(client, world["account"]["id"], member, "VIEWER", portfolio_id=world["portfolio"]["id"])

    assert inside.status_code == 201
    assert outside.status_code == 403
    assert portfolio_wide.status_code == 403


def test_granting_again_replaces_and_deleting_removes_access(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    member = add_member(db_session, world["account"]["id"], "m@acme.example", "MEMBER")
    first = grant(client, world["account"]["id"], member, "VIEWER", project_id=world["project"]["id"]).json()
    second = grant(client, world["account"]["id"], member, "MEMBER", project_id=world["project"]["id"], modules=["ISSUES"]).json()

    assert second["id"] == first["id"]
    assert (second["role"], second["modules"]) == ("MEMBER", ["ISSUES"])
    updated = client.patch(f"/api/v1/access-grants/{first['id']}", json={"modules": ["ISSUES", "RISKS"]}).json()
    assert updated["modules"] == ["ISSUES", "RISKS"]

    assert client.delete(f"/api/v1/access-grants/{first['id']}").status_code == 204
    act_as(member)
    assert client.get(f"/api/v1/projects/{world['project']['id']}").status_code == 403


def test_project_access_list_includes_inherited_grants(client: TestClient, db_session: Session, world: dict) -> None:
    portfolio_viewer = add_member(db_session, world["account"]["id"], "pv@acme.example", "VIEWER")
    project_member = add_member(db_session, world["account"]["id"], "pm@acme.example", "MEMBER")
    grant(client, world["account"]["id"], portfolio_viewer, "VIEWER", portfolio_id=world["portfolio"]["id"])
    grant(client, world["account"]["id"], project_member, "MEMBER", project_id=world["project"]["id"], modules=["TASKS"])

    grants = client.get(
        f"/api/v1/accounts/{world['account']['id']}/access-grants", params={"project_id": world["project"]["id"]}
    ).json()

    assert sorted((g["user"]["email"], g["scope_type"], g["inherited"]) for g in grants) == [
        ("pm@acme.example", "PROJECT", False),
        ("pv@acme.example", "PORTFOLIO", True),
    ]


def test_my_access_reports_effective_permissions(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    member = add_member(db_session, world["account"]["id"], "m@acme.example", "MEMBER")
    grant(client, world["account"]["id"], member, "VIEWER", program_id=world["program"]["id"])
    grant(client, world["account"]["id"], member, "MEMBER", project_id=world["project"]["id"], modules=["TESTING"])

    act_as(member)
    access = client.get(f"/api/v1/accounts/{world['account']['id']}/my-access").json()

    assert access["is_owner"] is False
    assert access["projects"] == {
        world["project"]["id"]: {
            "role": "MEMBER",
            "can_manage": False,
            "editable_modules": ["TESTING"],
            "visible_modules": [module.value for module in ProjectModule],
        }
    }
    assert access["programs"][world["program"]["id"]]["role"] == "VIEWER"
    assert world["other_program"]["id"] not in access["programs"]
    assert access["portfolios"] == {world["portfolio"]["id"]: None}


def test_invite_can_start_someone_on_a_project(client: TestClient, db_session: Session, world: dict) -> None:
    response = client.post(
        f"/api/v1/accounts/{world['account']['id']}/users/invite",
        json={
            "email": "new@acme.example",
            "role": "MEMBER",
            "access": {"project_id": world["project"]["id"], "modules": ["TASKS", "RISKS"]},
        },
    )

    assert response.status_code == 200
    created = db_session.scalar(select(AccessGrant).where(AccessGrant.user_id == UUID(response.json()["user_id"])))
    assert created is not None
    assert (str(created.project_id), created.role, created.modules) == (world["project"]["id"], "MEMBER", ["TASKS", "RISKS"])


def test_invite_rejects_starting_access_outside_the_account(client: TestClient, world: dict) -> None:
    other_account = client.post("/api/v1/accounts", json={"name": "Other", "slug": "other"}).json()

    response = client.post(
        f"/api/v1/accounts/{other_account['id']}/users/invite",
        json={"email": "new@acme.example", "role": "MEMBER", "access": {"project_id": world["project"]["id"]}},
    )

    assert response.status_code == 404


def test_member_sees_only_the_modules_they_were_given(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    project_id = world["project"]["id"]
    member = add_member(db_session, world["account"]["id"], "seer@acme.example", "MEMBER")
    response = grant(
        client, world["account"]["id"], member, "MEMBER",
        project_id=project_id, modules=["RISKS"], view_modules=["ISSUES"],
    )
    assert response.status_code == 201
    # Editing implies seeing, so RISKS joins the visible list.
    assert response.json()["view_modules"] == ["RISKS", "ISSUES"]
    risk = client.post(f"/api/v1/projects/{project_id}/risks", json={"title": "Findable risk"}).json()
    assumption = client.post(f"/api/v1/projects/{project_id}/assumptions", json={"description": "Hidden assumption"}).json()
    client.post(f"/api/v1/projects/{project_id}/tasks", json={"name": "Hidden task"})

    act_as(member)
    account_id = world["account"]["id"]
    assert client.get(f"/api/v1/projects/{project_id}/risks").status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}/issues").status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}/assumptions").status_code == 403
    assert client.get(f"/api/v1/assumptions/{assumption['id']}").status_code == 403
    assert client.get(f"/api/v1/projects/{project_id}/tasks").status_code == 403
    assert client.get(f"/api/v1/accounts/{account_id}/assumptions").json() == []
    assert [r["id"] for r in client.get(f"/api/v1/accounts/{account_id}/risks").json()] == [risk["id"]]
    assert client.post(f"/api/v1/projects/{project_id}/issues", json={"title": "Seen, not editable"}).status_code == 403

    overview = client.get(f"/api/v1/projects/{project_id}/overview").json()
    assert overview["stats"]["open_assumptions"] == 0

    access = client.get(f"/api/v1/accounts/{account_id}/my-access").json()
    assert access["projects"][project_id]["visible_modules"] == ["RISKS", "ISSUES"]
    assert access["projects"][project_id]["editable_modules"] == ["RISKS"]



def test_grants_combine_what_each_one_shows(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    project_id = world["project"]["id"]
    member = add_member(db_session, world["account"]["id"], "both@acme.example", "MEMBER")
    grant(client, world["account"]["id"], member, "VIEWER", program_id=world["program"]["id"], view_modules=["TASKS"])
    grant(client, world["account"]["id"], member, "VIEWER", project_id=project_id, view_modules=["RISKS"])

    act_as(member)
    access = client.get(f"/api/v1/accounts/{world['account']['id']}/my-access").json()
    assert access["projects"][project_id]["visible_modules"] == ["TASKS", "RISKS"]


def test_grant_update_can_clear_the_module_limit(client: TestClient, db_session: Session, world: dict) -> None:
    member = add_member(db_session, world["account"]["id"], "lim@acme.example", "MEMBER")
    created = grant(
        client, world["account"]["id"], member, "VIEWER", project_id=world["project"]["id"], view_modules=["TASKS"]
    ).json()

    kept = client.patch(f"/api/v1/access-grants/{created['id']}", json={"role": "VIEWER"}).json()
    cleared = client.patch(f"/api/v1/access-grants/{created['id']}", json={"view_modules": None}).json()

    assert kept["view_modules"] == ["TASKS"]
    assert cleared["view_modules"] is None
