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
from tests.helpers import give_role


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


def test_role_rights_decide_what_an_assigned_member_can_change(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    member = add_member(db_session, world["account"]["id"], "m@acme.example", "MEMBER")
    give_role(db_session, account_id=world["account"]["id"], user_id=member.id,
              permissions={"PROJECTS": ["VIEW", "EDIT"], "RAID": ["VIEW"]})
    assert grant(client, world["account"]["id"], member, "MEMBER", project_id=world["project"]["id"]).status_code == 201
    risk = client.post(f"/api/v1/projects/{world['project']['id']}/risks", json={"title": "Owner risk"}).json()

    act_as(member)
    project_id = world["project"]["id"]
    assert client.post(f"/api/v1/projects/{project_id}/tasks", json={"name": "Mine"}).status_code == 201
    assert client.get(f"/api/v1/projects/{project_id}/risks").status_code == 200
    created_risk = client.post(f"/api/v1/projects/{project_id}/risks", json={"title": "Nope"})
    assert created_risk.status_code == 403
    assert created_risk.json()["message"] == "Insufficient project permission."
    assert client.patch(f"/api/v1/risks/{risk['id']}", json={"title": "Edited"}).status_code == 403
    # Comments follow the area of what they are on.
    assert client.post(f"/api/v1/entities/RISK/{risk['id']}/comments", json={"body": "hi"}).status_code == 403
    # Not assigned to the other project.
    assert client.get(f"/api/v1/projects/{world['other_project']['id']}").status_code == 403


def test_grants_are_assignments_and_owners_need_none(client: TestClient, db_session: Session, world: dict) -> None:
    viewer = add_member(db_session, world["account"]["id"], "v@acme.example", "VIEWER")
    owner = db_session.scalar(select(User).where(User.email == "dev@example.com"))

    # The grant's role no longer matters: what a viewer can do comes from the Viewer role.
    assert grant(client, world["account"]["id"], viewer, "MEMBER", project_id=world["project"]["id"]).status_code == 201
    assert grant(client, world["account"]["id"], owner, "VIEWER", project_id=world["project"]["id"]).status_code == 400


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
    give_role(db_session, account_id=world["account"]["id"], user_id=member.id,
              permissions={"PORTFOLIOS": ["VIEW"], "PROGRAMS": ["VIEW"], "PROJECTS": ["VIEW"], "TESTING": ["VIEW", "EDIT"]})
    grant(client, world["account"]["id"], member, "VIEWER", program_id=world["program"]["id"])

    act_as(member)
    access = client.get(f"/api/v1/accounts/{world['account']['id']}/my-access").json()

    assert access["is_owner"] is False
    assert access["projects"] == {
        world["project"]["id"]: {
            "role": "MEMBER",
            "can_manage": False,
            "editable_modules": ["TESTING"],
            "visible_modules": ["TASKS", "TESTING", "FINANCIALS"],
        }
    }
    assert access["programs"][world["program"]["id"]]["role"] == "MEMBER"
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


def test_areas_a_role_cannot_view_are_hidden(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    project_id = world["project"]["id"]
    member = add_member(db_session, world["account"]["id"], "seer@acme.example", "MEMBER")
    give_role(db_session, account_id=world["account"]["id"], user_id=member.id,
              permissions={"PROJECTS": ["VIEW"], "RAID": ["VIEW", "EDIT"]})
    grant(client, world["account"]["id"], member, "MEMBER", project_id=project_id)
    risk = client.post(f"/api/v1/projects/{project_id}/risks", json={"title": "Findable risk"}).json()
    decision = client.post(f"/api/v1/projects/{project_id}/decisions", json={"title": "Hidden decision"}).json()

    act_as(member)
    account_id = world["account"]["id"]
    assert client.get(f"/api/v1/projects/{project_id}/risks").status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}/decisions").status_code == 403
    assert client.get(f"/api/v1/decisions/{decision['id']}").status_code == 403
    assert client.get(f"/api/v1/accounts/{account_id}/decisions").json() == []
    assert [r["id"] for r in client.get(f"/api/v1/accounts/{account_id}/risks").json()] == [risk["id"]]
    # View and Edit, but no Create.
    assert client.post(f"/api/v1/projects/{project_id}/issues", json={"title": "Not allowed"}).status_code == 403
    assert client.patch(f"/api/v1/risks/{risk['id']}", json={"title": "Allowed"}).status_code == 200

    access = client.get(f"/api/v1/accounts/{account_id}/my-access").json()
    assert access["projects"][project_id]["visible_modules"] == ["TASKS", "RISKS", "ISSUES", "ASSUMPTIONS", "DEPENDENCIES", "FINANCIALS"]
    assert access["projects"][project_id]["editable_modules"] == ["RISKS", "ISSUES", "ASSUMPTIONS", "DEPENDENCIES"]



def test_all_projects_scope_needs_no_assignment(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    member = add_member(db_session, world["account"]["id"], "all@acme.example", "MEMBER")
    give_role(db_session, account_id=world["account"]["id"], user_id=member.id, scope="ALL",
              permissions={"PORTFOLIOS": ["VIEW"], "PROGRAMS": ["VIEW"], "PROJECTS": ["VIEW"]})

    act_as(member)
    assert client.get(f"/api/v1/projects/{world['project']['id']}").status_code == 200
    assert client.get(f"/api/v1/projects/{world['other_project']['id']}").status_code == 200
    assert len(client.get(f"/api/v1/accounts/{world['account']['id']}/portfolios").json()) == 1


def test_a_resource_allocation_assigns_the_project(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    member = add_member(db_session, world["account"]["id"], "alloc@acme.example", "VIEWER")
    resource = client.post(
        f"/api/v1/accounts/{world['account']['id']}/resources", json={"name": "Alloc", "user_id": str(member.id)}
    ).json()
    project_id = world["project"]["id"]

    act_as(member)
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 403
    act_as(None)
    allocated = client.put(f"/api/v1/resources/{resource['id']}/project-allocations/{project_id}", json={"weekly_hours": 10})
    assert allocated.status_code in (200, 204), allocated.text

    act_as(member)
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 200
    assert client.get(f"/api/v1/projects/{world['other_project']['id']}").status_code == 403


def test_creating_and_deleting_need_their_own_rights(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    member = add_member(db_session, world["account"]["id"], "pm@acme.example", "MEMBER")
    give_role(db_session, account_id=world["account"]["id"], user_id=member.id,
              permissions={"PROGRAMS": ["VIEW"], "PROJECTS": ["VIEW", "EDIT"], "RAID": ["VIEW", "CREATE", "EDIT"]})
    grant(client, world["account"]["id"], member, "MEMBER", program_id=world["program"]["id"])
    risk = client.post(f"/api/v1/projects/{world['project']['id']}/risks", json={"title": "Keep"}).json()

    act_as(member)
    assert client.post(f"/api/v1/programs/{world['program']['id']}/projects", json={"name": "New"}).status_code == 403
    assert client.delete(f"/api/v1/projects/{world['project']['id']}").status_code == 403
    assert client.post(f"/api/v1/projects/{world['project']['id']}/risks", json={"title": "Mine"}).status_code == 201
    assert client.delete(f"/api/v1/risks/{risk['id']}").status_code == 403
    assert client.patch(f"/api/v1/projects/{world['project']['id']}", json={"name": "Renamed"}).status_code == 200


def test_approving_needs_the_approve_right(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    member = add_member(db_session, world["account"]["id"], "gov@acme.example", "MEMBER")
    role = give_role(db_session, account_id=world["account"]["id"], user_id=member.id,
                     permissions={"PROJECTS": ["VIEW"], "GOVERNANCE": ["VIEW", "CREATE", "EDIT"]})
    grant(client, world["account"]["id"], member, "MEMBER", project_id=world["project"]["id"])
    decision = client.post(f"/api/v1/projects/{world['project']['id']}/decisions", json={"title": "Pick a vendor"}).json()

    act_as(member)
    approve = client.patch(f"/api/v1/decisions/{decision['id']}", json={"status": "APPROVED"})
    assert approve.status_code == 403
    assert client.patch(f"/api/v1/decisions/{decision['id']}", json={"status": "IN_REVIEW"}).status_code == 200

    role.permissions = {**role.permissions, "GOVERNANCE": ["VIEW", "CREATE", "EDIT", "APPROVE"]}
    db_session.commit()
    assert client.patch(f"/api/v1/decisions/{decision['id']}", json={"status": "APPROVED"}).status_code == 200


def test_owners_manage_roles_and_seats_follow_the_rights(client: TestClient, db_session: Session, world: dict) -> None:
    account_id = world["account"]["id"]
    roles = client.get(f"/api/v1/accounts/{account_id}/roles").json()
    assert [r["name"] for r in roles] == [
        "Owner", "Portfolio Manager", "Program Manager", "Project Manager", "Contributor", "Viewer",
    ]
    assert {r["name"]: r["seat"] for r in roles}["Viewer"] == "VIEWER"

    analyst = client.post(
        f"/api/v1/accounts/{account_id}/roles",
        json={"name": "Analyst", "permissions": {"REPORTING": ["VIEW"]}, "scope": "ALL"},
    ).json()
    assert analyst["seat"] == "VIEWER"
    invited = client.post(
        f"/api/v1/accounts/{account_id}/users/invite",
        json={"email": "ana@acme.example", "first_name": "Ana", "last_name": "Lyst", "title": "Analyst",
              "department": "Finance", "role_id": analyst["id"]},
    )
    assert invited.status_code == 200, invited.text
    member = db_session.scalar(select(AccountMember).where(AccountMember.user_id == UUID(invited.json()["user_id"])))
    assert (member.role, str(member.role_id)) == ("VIEWER", analyst["id"])
    listed = {u["email"]: u for u in client.get(f"/api/v1/accounts/{account_id}/users").json()}
    assert listed["ana@acme.example"]["role_name"] == "Analyst"
    assert (listed["ana@acme.example"]["full_name"], listed["ana@acme.example"]["department"]) == ("Ana Lyst", "Finance")

    # Giving the role an edit right makes it a full seat for everyone holding it.
    updated = client.patch(f"/api/v1/account-roles/{analyst['id']}", json={"permissions": {"REPORTING": ["VIEW"], "RAID": ["EDIT"]}})
    assert updated.json()["seat"] == "MEMBER"
    assert updated.json()["permissions"]["RAID"] == ["VIEW", "EDIT"]
    db_session.expire_all()
    assert db_session.get(AccountMember, member.id).role == "MEMBER"

    assert client.delete(f"/api/v1/account-roles/{analyst['id']}").status_code == 409
    owner_role = next(r for r in roles if r["key"] == "OWNER")
    assert client.patch(f"/api/v1/account-roles/{owner_role['id']}", json={"scope": "ASSIGNED"}).status_code == 400


def test_grant_update_can_clear_the_module_limit(client: TestClient, db_session: Session, world: dict) -> None:
    member = add_member(db_session, world["account"]["id"], "lim@acme.example", "MEMBER")
    created = grant(
        client, world["account"]["id"], member, "VIEWER", project_id=world["project"]["id"], view_modules=["TASKS"]
    ).json()

    kept = client.patch(f"/api/v1/access-grants/{created['id']}", json={"role": "VIEWER"}).json()
    cleared = client.patch(f"/api/v1/access-grants/{created['id']}", json={"view_modules": None}).json()

    assert kept["view_modules"] == ["TASKS"]
    assert cleared["view_modules"] is None
