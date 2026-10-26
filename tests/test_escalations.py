from collections.abc import Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.main import app
from app.models.account_member import AccountMember
from app.models.activity_log import ActivityLog
from app.models.notification import Notification
from app.models.resource import Resource
from app.models.user import User
from app.services.email_delivery import render_escalation_email
from app.services.notification_preferences import NotificationPreferenceService
from tests.ai_support import acting_as, add_member, create_account, db_session, dev_user  # noqa: F401


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: Settings(auth_mode="local", environment="local")
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def add_resource(db_session: Session, world: dict, name: str, department: str, user: User | None = None) -> Resource:
    resource = Resource(account_id=UUID(world["account"]["id"]), name=name, department=department, user_id=user.id if user else None)
    db_session.add(resource)
    db_session.commit()
    return resource


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> dict:
    hierarchy = create_account(client)
    hierarchy["alex"] = add_member(db_session, hierarchy, "alex@example.com", modules=["RISKS"])
    hierarchy["blair"] = add_member(db_session, hierarchy, "blair@example.com", modules=["RISKS"])
    # A member with no access to the project the risk is in.
    outsider = User(email="olive@example.com", full_name="Olive")
    db_session.add(outsider)
    db_session.flush()
    db_session.add(AccountMember(account_id=UUID(hierarchy["account"]["id"]), user_id=outsider.id, role="MEMBER"))
    db_session.commit()
    hierarchy["alex_resource"] = add_resource(db_session, hierarchy, "Alex Ng", "PMO", hierarchy["alex"])
    add_resource(db_session, hierarchy, "Blair Ito", "pmo ", hierarchy["blair"])
    hierarchy["casey_resource"] = add_resource(db_session, hierarchy, "Casey Contractor", "PMO")
    add_resource(db_session, hierarchy, "Olive", "PMO", outsider)
    response = client.post(f"/api/v1/projects/{hierarchy['apollo']['id']}/risks", json={"title": "Vendor slips", "priority": "high"})
    assert response.status_code == 201, response.text
    hierarchy["risk"] = response.json()
    return hierarchy


def escalate(client: TestClient, world: dict, body: dict):
    return client.post(f"/api/v1/raid/risk/{world['risk']['id']}/escalate", json=body)


def test_escalating_to_a_person_notifies_them_with_the_details(client: TestClient, db_session: Session, world: dict) -> None:
    response = escalate(client, world, {"resource_id": str(world["alex_resource"].id), "note": "Needs a decision today."})
    assert response.status_code == 200, response.text
    assert response.json() == {"escalated_to": "Alex Ng", "notified": [{"name": "Alex Ng", "emailed": False}], "skipped": []}

    notification = db_session.scalars(select(Notification)).one()
    assert notification.user_id == world["alex"].id and notification.notification_type == "ESCALATION"
    assert notification.title == "Dev User escalated a risk to Alex Ng" and notification.message == "Needs a decision today."
    assert notification.details["reference"] == world["risk"]["risk_number"]
    assert notification.details["project"] == "Apollo" and notification.details["priority"] == "High"
    activity = db_session.scalars(select(ActivityLog).where(ActivityLog.action == "ESCALATED")).one()
    assert activity.new_values["escalated_to"] == "Alex Ng"
    # Escalations skip people's email settings: they always go out straight away.
    assert NotificationPreferenceService(db_session).frequency_for(world["alex"].id, "ESCALATION").value == "IMMEDIATE"


def test_escalating_to_a_team_reaches_everyone_who_can_open_it(client: TestClient, db_session: Session, world: dict) -> None:
    response = escalate(client, world, {"team": "PMO"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["escalated_to"] == "PMO team"
    assert sorted(n["name"] for n in body["notified"]) == ["Alex Ng", "Blair Ito"]
    assert {s["name"]: s["reason"] for s in body["skipped"]} == {
        "Casey Contractor": "No Project Hub login, so there's no email address.",
        "Olive": "They can't open this risk.",
    }
    assert len(db_session.scalars(select(Notification)).all()) == 2


def test_escalation_needs_someone_reachable(client: TestClient, db_session: Session, world: dict) -> None:
    assert escalate(client, world, {}).status_code == 422
    assert escalate(client, world, {"team": "Nobody"}).status_code == 404
    unreachable = escalate(client, world, {"resource_id": str(world["casey_resource"].id)})
    assert unreachable.status_code == 400 and "Casey Contractor" in unreachable.text
    assert db_session.scalars(select(Notification)).all() == []
    assert db_session.scalars(select(ActivityLog).where(ActivityLog.action == "ESCALATED")).all() == []


def test_escalating_needs_edit_rights_on_the_item(client: TestClient, db_session: Session, world: dict) -> None:
    viewer = add_member(db_session, world, "vic@example.com", modules=[], view_modules=["RISKS"])
    with acting_as(viewer):
        assert escalate(client, world, {"team": "PMO"}).status_code == 403


def test_escalation_email_shows_the_item_and_the_note(client: TestClient, db_session: Session, world: dict) -> None:
    escalate(client, world, {"resource_id": str(world["alex_resource"].id), "note": "Needs a decision today."})
    notification = db_session.scalars(select(Notification)).one()
    email = render_escalation_email(
        settings=Settings(app_url="https://app.example.com", email_token_secret="s"),
        user=world["alex"],
        notification=notification,
        account_name="Acme",
    )
    assert email.to == "alex@example.com"
    assert email.subject == f"Escalation: {world['risk']['risk_number']} · Vendor slips"
    assert "An escalation needs your attention" in email.html
    assert "Dev User escalated a risk to Alex Ng. Please review the details below." in email.text
    assert "Escalated to: Alex Ng" in email.text and "Note from Dev User:" in email.text
    assert f"https://app.example.com/RiskLog?tab=risks&amp;id={world['risk']['id']}" in email.html
