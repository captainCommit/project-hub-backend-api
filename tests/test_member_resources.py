from fastapi.testclient import TestClient
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.resource import Resource
from app.models.user import User
from tests.test_account_dashboard import client, create_account, db_session  # noqa: F401 - fixtures


def resources_for(db: Session, account_id: str) -> list[Resource]:
    return list(db.scalars(select(Resource).where(Resource.account_id == UUID(account_id))).all())


def invite(client: TestClient, account_id: str, email: str, full_name: str) -> None:
    response = client.post(
        f"/api/v1/accounts/{account_id}/users/invite",
        json={"email": email, "full_name": full_name, "role": "MEMBER"},
    )
    assert response.status_code in (200, 201), response.text


def test_account_owner_and_invited_members_get_linked_resources(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    me = client.get("/api/v1/me").json()

    invite(client, account["id"], "sam@example.com", "Sam Lee")

    resources = {r.name: r for r in resources_for(db_session, account["id"])}
    sam = db_session.scalar(select(User).where(User.email == "sam@example.com"))
    assert str(resources[me["full_name"]].user_id) == me["id"]
    assert resources["Sam Lee"].user_id == sam.id
    # Inviting again doesn't add a second resource.
    invite(client, account["id"], "sam@example.com", "Sam Lee")
    assert len(resources_for(db_session, account["id"])) == 2


def test_existing_resource_with_the_same_name_is_linked_not_duplicated(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    existing = client.post(f"/api/v1/accounts/{account['id']}/resources", json={"name": "  pat jones "})
    assert existing.status_code == 201

    invite(client, account["id"], "pat@example.com", "Pat Jones")

    pats = [r for r in resources_for(db_session, account["id"]) if r.name.strip().lower() == "pat jones"]
    assert len(pats) == 1
    assert str(pats[0].id) == existing.json()["id"]
    assert pats[0].user_id is not None


def test_a_login_can_be_linked_to_only_one_resource(client: TestClient) -> None:
    account = create_account(client)
    me = client.get("/api/v1/me").json()
    other = client.post(f"/api/v1/accounts/{account['id']}/resources", json={"name": "Contractor"}).json()

    response = client.patch(f"/api/v1/resources/{other['id']}", json={"user_id": me["id"]})

    assert response.status_code == 400
    assert "already linked" in response.text
