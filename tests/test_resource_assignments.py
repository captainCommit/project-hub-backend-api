"""Resources > Capacity > Add Resource, and what a person limited to assigned projects can see across the app."""

from collections.abc import Callable, Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.account_member import AccountMember
from app.models.resource import Resource
from app.models.user import User
from app.services.auth import get_current_user
from tests.helpers import give_role
from tests.test_search import sqlite_similarity

CONTRIBUTOR = {
    "PORTFOLIOS": ["VIEW"], "PROGRAMS": ["VIEW"], "PROJECTS": ["VIEW", "EDIT"], "RAID": ["VIEW", "CREATE", "EDIT"],
    "RESOURCES": ["VIEW"], "REPORTING": ["VIEW"],
}
RESOURCE_MANAGER = {**CONTRIBUTOR, "RESOURCES": ["VIEW", "CREATE", "EDIT", "DELETE"]}


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def register_similarity(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.create_function("similarity", 2, sqlite_similarity)

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
    def switch(user: User | None) -> None:
        if user is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = lambda: user

    yield switch
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def world(client: TestClient) -> dict[str, dict]:
    """Owned by the dev user. Portfolio > Program A > Project A (with a risk and a task); Program B > Project B (same)."""
    account = client.post("/api/v1/accounts", json={"name": "Acme", "slug": "acme"}).json()
    portfolio = client.post(f"/api/v1/accounts/{account['id']}/portfolios", json={"name": "Portfolio"}).json()
    program = client.post(f"/api/v1/portfolios/{portfolio['id']}/programs", json={"name": "Program A"}).json()
    other_program = client.post(f"/api/v1/portfolios/{portfolio['id']}/programs", json={"name": "Program B"}).json()
    project = client.post(f"/api/v1/programs/{program['id']}/projects", json={"name": "Project Alpha"}).json()
    other_project = client.post(f"/api/v1/programs/{other_program['id']}/projects", json={"name": "Project Bravo"}).json()
    for target, word in ((project, "Alpha"), (other_project, "Bravo")):
        assert client.post(f"/api/v1/projects/{target['id']}/risks", json={"title": f"{word} vendor risk"}).status_code == 201
        assert client.post(f"/api/v1/projects/{target['id']}/tasks", json={"name": f"{word} build task"}).status_code == 201
    return {
        "account": account, "portfolio": portfolio, "program": program, "other_program": other_program,
        "project": project, "other_project": other_project,
    }


def add_member(db_session: Session, account_id: str, email: str, *, title: str | None = None, department: str | None = None,
               permissions: dict[str, list[str]] = CONTRIBUTOR) -> User:
    """A member who just joined: their resource exists (unlisted), as it does after an invite."""
    from app.services.member_resources import ensure_member_resource

    user = User(email=email, full_name=email.split("@")[0].title(), cognito_sub=f"sub-{email}", title=title, department=department)
    db_session.add(user)
    db_session.flush()
    db_session.add(AccountMember(account_id=UUID(account_id), user_id=user.id, role="MEMBER"))
    ensure_member_resource(db_session, account_id=UUID(account_id), user=user)
    db_session.commit()
    give_role(db_session, account_id=account_id, user_id=user.id, permissions=permissions)
    return user


def resources_url(world: dict) -> str:
    return f"/api/v1/accounts/{world['account']['id']}/resources"


def listed(client: TestClient, world: dict) -> dict[str, dict]:
    return {r["name"]: r for r in client.get(resources_url(world)).json() if r["listed"]}


def test_add_resource_picks_a_member_and_fills_in_their_profile(client: TestClient, db_session: Session, world: dict) -> None:
    aisha = add_member(db_session, world["account"]["id"], "aisha@acme.example", title="Business Analyst", department="PMO")
    assert "Aisha" not in listed(client, world), "members aren't on Resources until someone adds them"

    response = client.post(resources_url(world), json={
        "user_id": str(aisha.id),
        "weekly_capacity_hours": 37.5,
        "portfolio_ids": [world["portfolio"]["id"]],
        "program_ids": [world["program"]["id"]],
        "project_ids": [world["project"]["id"]],
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["name"], body["role"], body["department"], body["weekly_capacity_hours"]) == (
        "Aisha", "Business Analyst", "PMO", "37.50"
    )
    assert body["listed"] is True
    assert body["project_ids"] == [world["project"]["id"]]
    assert body["program_ids"] == [world["program"]["id"]]
    assert body["portfolio_ids"] == [world["portfolio"]["id"]]
    # The same login, not a second resource.
    assert db_session.query(Resource).filter(Resource.user_id == aisha.id).count() == 1

    again = client.post(resources_url(world), json={"user_id": str(aisha.id)})
    assert again.status_code == 400 and "already on Resources" in again.text


def test_picking_a_project_brings_its_program_and_portfolio(client: TestClient, db_session: Session, world: dict) -> None:
    ben = add_member(db_session, world["account"]["id"], "ben@acme.example")
    body = client.post(resources_url(world), json={"user_id": str(ben.id), "project_ids": [world["other_project"]["id"]]}).json()
    assert body["program_ids"] == [world["other_program"]["id"]]
    assert body["portfolio_ids"] == [world["portfolio"]["id"]]

    # Editing replaces the set.
    updated = client.patch(f"/api/v1/resources/{body['id']}", json={
        "portfolio_ids": [world["portfolio"]["id"]], "program_ids": [world["program"]["id"]], "project_ids": [world["project"]["id"]],
    }).json()
    assert updated["project_ids"] == [world["project"]["id"]]
    assert updated["program_ids"] == [world["program"]["id"]]


def test_a_project_assignment_opens_that_project_and_nothing_else(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    cara = add_member(db_session, world["account"]["id"], "cara@acme.example")
    act_as(cara)
    assert client.get(f"/api/v1/projects/{world['project']['id']}").status_code == 403

    act_as(None)
    resource = client.post(resources_url(world), json={
        "user_id": str(cara.id), "portfolio_ids": [world["portfolio"]["id"]], "program_ids": [world["program"]["id"]],
    }).json()
    act_as(cara)
    # A portfolio or program on its own records where they work; it doesn't open the projects in it.
    assert client.get(f"/api/v1/projects/{world['project']['id']}").status_code == 403

    act_as(None)
    client.patch(f"/api/v1/resources/{resource['id']}", json={"project_ids": [world["project"]["id"]]})
    act_as(cara)
    assert client.get(f"/api/v1/projects/{world['project']['id']}").status_code == 200
    assert client.get(f"/api/v1/projects/{world['other_project']['id']}").status_code == 403

    # Removing them from Resources takes it away again.
    act_as(None)
    assert client.delete(f"/api/v1/resources/{resource['id']}").status_code == 204
    act_as(cara)
    assert client.get(f"/api/v1/projects/{world['project']['id']}").status_code == 403
    act_as(None)
    assert "Cara" not in listed(client, world)
    # Their resource is kept (unlisted) so work assigned to them still counts.
    assert db_session.get(Resource, UUID(resource["id"])).is_active


def test_adding_and_removing_resources_need_their_own_rights(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    dee = add_member(db_session, world["account"]["id"], "dee@acme.example")
    editor = add_member(db_session, world["account"]["id"], "ed@acme.example",
                        permissions={**CONTRIBUTOR, "RESOURCES": ["VIEW", "EDIT"]})
    client.post(resources_url(world), json={"user_id": str(editor.id), "project_ids": [world["project"]["id"]]})

    act_as(editor)
    response = client.post(resources_url(world), json={"user_id": str(dee.id), "project_ids": [world["project"]["id"]]})
    assert response.status_code == 403
    act_as(None)
    added = client.post(resources_url(world), json={"user_id": str(dee.id), "project_ids": [world["project"]["id"]]}).json()
    act_as(editor)
    assert client.patch(f"/api/v1/resources/{added['id']}", json={"non_project_hours": 4}).status_code == 200
    assert client.delete(f"/api/v1/resources/{added['id']}").status_code == 403


def test_assigning_is_limited_to_what_the_assigner_can_see(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    manager = add_member(db_session, world["account"]["id"], "mgr@acme.example", permissions=RESOURCE_MANAGER)
    eve = add_member(db_session, world["account"]["id"], "eve@acme.example")
    client.post(resources_url(world), json={"user_id": str(manager.id), "project_ids": [world["project"]["id"]]})
    # The owner put Eve on both projects.
    eve_resource = client.post(resources_url(world), json={
        "user_id": str(eve.id), "project_ids": [world["project"]["id"], world["other_project"]["id"]],
    }).json()

    act_as(manager)
    seen = listed(client, world)["Eve"]
    assert seen["project_ids"] == [world["project"]["id"]], "assignments outside the viewer's projects stay hidden"
    refused = client.patch(f"/api/v1/resources/{eve_resource['id']}", json={"project_ids": [world["other_project"]["id"]]})
    assert refused.status_code == 403
    # Clearing what they can see leaves Project Bravo, which they can't, in place.
    cleared = client.patch(f"/api/v1/resources/{eve_resource['id']}", json={"project_ids": []})
    assert cleared.status_code == 200 and cleared.json()["project_ids"] == []
    act_as(None)
    assert client.get(f"/api/v1/resources/{eve_resource['id']}").json()["project_ids"] == [world["other_project"]["id"]]


def test_resources_pages_show_only_people_in_the_viewers_projects(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    viewer = add_member(db_session, world["account"]["id"], "vic@acme.example")
    alpha = add_member(db_session, world["account"]["id"], "alpha@acme.example")
    bravo = add_member(db_session, world["account"]["id"], "bravo@acme.example")
    for user, project in ((viewer, "project"), (alpha, "project"), (bravo, "other_project")):
        client.post(resources_url(world), json={"user_id": str(user.id), "project_ids": [world[project]["id"]]})
    by_name = listed(client, world)
    client.patch(f"/api/v1/resources/{by_name['Bravo']['id']}", json={"hourly_rate": 120})
    for name, project in (("Alpha", "project"), ("Bravo", "other_project")):
        client.put(f"/api/v1/resources/{by_name[name]['id']}/project-allocations/{world[project]['id']}", json={"weekly_hours": 5})

    act_as(viewer)
    everyone = {r["name"]: r for r in client.get(resources_url(world)).json()}
    assert {name for name, r in everyone.items() if r["listed"]} == {"Vic", "Alpha"}
    # Bravo's name is still there for people pickers, but nothing about their work or rate.
    assert everyone["Bravo"]["listed"] is False and everyone["Bravo"]["hourly_rate"] is None
    assert everyone["Bravo"]["project_ids"] == []
    overrides = client.get(f"/api/v1/accounts/{world['account']['id']}/resource-project-allocations").json()
    assert {o["project_id"] for o in overrides} == {world["project"]["id"]}
    assert client.get(f"/api/v1/resources/{by_name['Bravo']['id']}").status_code == 404
    found = client.get("/api/v1/search", params={"q": "Bravo", "entity_types": "RESOURCE"}).json()["results"]
    assert found == []

    # Without Resources > View, nobody is listed.
    give_role(db_session, account_id=world["account"]["id"], user_id=viewer.id,
              permissions={**CONTRIBUTOR, "RESOURCES": []})
    assert not [r for r in client.get(resources_url(world)).json() if r["listed"]]


def test_an_assigned_user_sees_only_their_projects_everywhere(
    client: TestClient, db_session: Session, world: dict, act_as: Callable
) -> None:
    """Home, dashboard counts, lists, search, AI-visible data and direct links all stop at the assigned project."""
    pat = add_member(db_session, world["account"]["id"], "pat@acme.example")
    client.post(resources_url(world), json={"user_id": str(pat.id), "project_ids": [world["project"]["id"]]})
    account_id = world["account"]["id"]

    act_as(pat)
    # Direct links to anything else are refused.
    assert client.get(f"/api/v1/projects/{world['other_project']['id']}").status_code == 403
    assert client.get(f"/api/v1/projects/{world['other_project']['id']}/overview").status_code == 403
    assert client.get(f"/api/v1/projects/{world['other_project']['id']}/risks").status_code == 403
    assert client.get(f"/api/v1/programs/{world['other_program']['id']}").status_code == 403
    # Dashboard KPIs count only their project.
    summary = client.get(f"/api/v1/accounts/{account_id}/dashboard").json()["summary"]
    assert (summary["project_count"], summary["program_count"], summary["total_tasks"]) == (1, 1, 1)
    assert client.get(f"/api/v1/accounts/{account_id}/dashboard",
                      params={"project_id": world["other_project"]["id"]}).status_code == 403
    # Lists that feed exports and reports.
    risks = client.get(f"/api/v1/accounts/{account_id}/risks").json()
    risks = risks["items"] if isinstance(risks, dict) else risks
    assert [r["title"] for r in risks] == ["Alpha vendor risk"]
    tasks = client.get(f"/api/v1/accounts/{account_id}/tasks").json()
    tasks = tasks["items"] if isinstance(tasks, dict) else tasks
    assert [t["name"] for t in tasks] == ["Alpha build task"]
    sidebar = client.get(f"/api/v1/accounts/{account_id}/sidebar").json()
    assert [p["name"] for pf in sidebar["portfolios"] for pr in pf["programs"] for p in pr["projects"]] == ["Project Alpha"]
    # Search (also what the AI assistant searches with).
    found = client.get("/api/v1/search", params={"q": "vendor risk"}).json()["results"]
    assert [r["title"] for r in found] == ["Alpha vendor risk"]
    found = client.get("/api/v1/search", params={"q": "Bravo"}).json()["results"]
    assert found == []
    # Home.
    assert client.get(f"/api/v1/accounts/{account_id}/me/home").status_code == 200
    # What the UI uses to show and hide areas.
    access = client.get(f"/api/v1/accounts/{account_id}/my-access").json()
    assert set(access["projects"]) == {world["project"]["id"]}
    assert access["rights"]["RESOURCES"] == ["VIEW"] and access["scope_all"] is False
