from collections.abc import Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.main import app
from app.models.notification import Notification
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.user import User
from app.services.access import can_see_entity
from tests.ai_support import acting_as, add_member, create_account, db_session, dev_user  # noqa: F401


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: Settings(auth_mode="local", environment="local")
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def admin(db_session: Session) -> User:
    """A platform admin who isn't a member of the customer's account."""
    user = User(email="support@projecthub.com", full_name="Sam Support", is_platform_admin=True)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> dict:
    hierarchy = create_account(client)
    hierarchy["alex"] = add_member(db_session, hierarchy, "alex@example.com", modules=["TASKS"])
    hierarchy["blair"] = add_member(db_session, hierarchy, "blair@example.com", modules=["TASKS"])
    with acting_as(hierarchy["alex"]):
        response = client.post(
            f"/api/v1/accounts/{hierarchy['account']['id']}/support-tickets",
            json={"category": "bug", "subject": "Export fails", "description": "The PDF export spins forever."},
        )
    assert response.status_code == 201, response.text
    hierarchy["ticket"] = response.json()
    return hierarchy


def test_platform_admins_see_every_accounts_tickets(client: TestClient, world: dict, admin: User) -> None:
    with acting_as(admin):
        tickets = client.get("/api/v1/admin/support-tickets").json()
        summary = client.get("/api/v1/admin/support-tickets/summary").json()
        found = client.get("/api/v1/admin/support-tickets", params={"search": "pdf export"}).json()
        none = client.get("/api/v1/admin/support-tickets", params={"status": "CLOSED"}).json()
        everything = client.get("/api/v1/admin/support-tickets", params={"status": "ALL"}).json()
    (ticket,) = tickets
    assert ticket["account_name"] == world["account"]["name"] and ticket["requester_email"] == "alex@example.com"
    assert ticket["needs_reply"] is True and ticket["messages"] == []
    assert summary == {"active": 1, "needs_reply": 1}
    assert [t["id"] for t in found] == [ticket["id"]] and none == [] and len(everything) == 1

    with acting_as(world["alex"]):
        assert client.get("/api/v1/admin/support-tickets").status_code == 403


def test_support_reply_reaches_the_requester(client: TestClient, db_session: Session, world: dict, admin: User) -> None:
    ticket_id = world["ticket"]["id"]
    with acting_as(admin):
        replied = client.post(f"/api/v1/admin/support-tickets/{ticket_id}/messages", json={"content": "Fixed in today's release."})
    assert replied.status_code == 200, replied.text
    body = replied.json()
    assert body["status"] == "AWAITING_RESPONSE" and body["needs_reply"] is False
    reply = body["messages"][-1]
    assert (reply["role"], reply["author"], reply["agent"]) == ("support", "Project Hub Support", "Sam Support")

    notification = db_session.scalars(select(Notification)).one()
    assert notification.user_id == world["alex"].id and notification.notification_type == "SUPPORT_REPLY"
    assert db_session.scalars(select(PlatformAuditEvent.action).where(PlatformAuditEvent.action.like("support.%"))).all() == ["support.ticket_replied"]

    # The requester sees it in Help Center, and answering puts it back in the queue.
    with acting_as(world["alex"]):
        seen = client.get(f"/api/v1/support-tickets/{ticket_id}").json()
        assert seen["messages"][-1]["author"] == "Project Hub Support"
        assert client.post(f"/api/v1/support-tickets/{ticket_id}/messages", json={"content": "Works now, thanks"}).json()["status"] == "OPEN"
    with acting_as(admin):
        assert client.get(f"/api/v1/admin/support-tickets/{ticket_id}").json()["needs_reply"] is True


def test_owner_replies_as_themselves_not_as_support(client: TestClient, world: dict) -> None:
    replied = client.post(f"/api/v1/support-tickets/{world['ticket']['id']}/messages", json={"content": "Seeing this too."})
    message = replied.json()["messages"][-1]
    assert (message["role"], message["author"]) == ("user", "Dev User")


def test_admin_changes_status_and_priority(client: TestClient, db_session: Session, world: dict, admin: User) -> None:
    with acting_as(admin):
        updated = client.patch(f"/api/v1/admin/support-tickets/{world['ticket']['id']}", json={"status": "resolved", "priority": "high"})
        active = client.get("/api/v1/admin/support-tickets").json()
    assert (updated.json()["status"], updated.json()["priority"]) == ("RESOLVED", "HIGH")
    assert active == []
    event = db_session.scalars(select(PlatformAuditEvent).where(PlatformAuditEvent.action.like("support.%"))).one()
    assert event.action == "support.ticket_updated" and event.details == {"status": "RESOLVED", "priority": "HIGH"}


def test_only_the_requester_owners_and_support_see_a_ticket(client: TestClient, db_session: Session, world: dict, admin: User) -> None:
    ticket_id = world["ticket"]["id"]
    account_id = world["account"]["id"]
    owner = dev_user(db_session)
    assert can_see_entity(db_session, account_id=UUID(account_id), user=world["alex"], entity_type="SUPPORT_TICKET", entity_id=UUID(ticket_id))
    assert can_see_entity(db_session, account_id=UUID(account_id), user=owner, entity_type="SUPPORT_TICKET", entity_id=UUID(ticket_id))
    assert not can_see_entity(db_session, account_id=UUID(account_id), user=world["blair"], entity_type="SUPPORT_TICKET", entity_id=UUID(ticket_id))

    attachments = f"/api/v1/entities/SUPPORT_TICKET/{ticket_id}/attachments"
    with acting_as(world["blair"]):
        assert client.get(attachments).status_code == 404
    with acting_as(world["alex"]):
        assert client.get(attachments).status_code == 200
    with acting_as(admin):
        assert client.get(attachments).status_code == 200
