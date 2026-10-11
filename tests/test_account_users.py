from collections.abc import Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.services.account_users as account_user_service
from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.account import Account
from app.models.account_member import AccountMember, AccountMemberRole
from app.models.user import User
from app.services.auth import get_current_user


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


def add_account_user(
    db_session: Session,
    *,
    account_id: str | UUID,
    email: str,
    full_name: str,
    role: AccountMemberRole = AccountMemberRole.MEMBER,
    cognito_sub: str | None = None,
) -> User:
    user = User(email=email, full_name=full_name, cognito_sub=cognito_sub)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        AccountMember(
            account_id=UUID(str(account_id)),
            user_id=user.id,
            role=role.value,
        )
    )
    db_session.commit()
    return user


def create_private_account(db_session: Session) -> Account:
    other_user = User(email="private-owner@example.com", full_name="Private Owner")
    db_session.add(other_user)
    db_session.flush()
    account = Account(name="Private Account", slug="private-account", created_by=other_user.id)
    db_session.add(account)
    db_session.flush()
    db_session.add(
        AccountMember(
            account_id=account.id,
            user_id=other_user.id,
            role=AccountMemberRole.OWNER.value,
        )
    )
    db_session.commit()
    return account


def set_current_user_role(db_session: Session, account_id: str | UUID, role: AccountMemberRole) -> None:
    membership = db_session.scalar(select(AccountMember).where(AccountMember.account_id == UUID(str(account_id))))
    assert membership is not None
    membership.role = role.value
    db_session.commit()


def make_current_user_regular(client: TestClient, db_session: Session) -> None:
    """The local dev user is a platform admin; turn that off to test member-only rules."""
    client.get("/api/v1/me")
    current = db_session.scalar(select(User).where(User.email == "dev@example.com"))
    assert current is not None
    current.is_platform_admin = False
    db_session.commit()


def invite_user(
    client: TestClient,
    account_id: str | UUID,
    *,
    email: str = "invitee@example.com",
    full_name: str = "Invitee User",
    role: str = "MEMBER",
    update_existing: bool | None = None,
):
    payload: dict[str, object] = {
        "email": email,
        "full_name": full_name,
        "role": role,
    }
    if update_existing is not None:
        payload["update_existing"] = update_existing
    return client.post(f"/api/v1/accounts/{account_id}/users/invite", json=payload)


def test_account_member_can_list_account_users(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    add_account_user(db_session, account_id=account["id"], email="alice@example.com", full_name="Alice Example")

    response = client.get(f"/api/v1/accounts/{account['id']}/users")

    assert response.status_code == 200
    emails = {user["email"] for user in response.json()}
    assert emails == {"dev@example.com", "alice@example.com"}


def test_non_member_cannot_list_account_users(client: TestClient, db_session: Session) -> None:
    account = create_private_account(db_session)
    make_current_user_regular(client, db_session)

    response = client.get(f"/api/v1/accounts/{account.id}/users")

    assert response.status_code == 403
    assert response.json()["message"] == "Account access denied."


def test_search_account_users_by_email(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    add_account_user(db_session, account_id=account["id"], email="jane.special@example.com", full_name="Jane Doe")

    response = client.get(f"/api/v1/accounts/{account['id']}/users/search", params={"q": "special"})

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["email"] == "jane.special@example.com"


def test_search_account_users_by_full_name(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    add_account_user(db_session, account_id=account["id"], email="alex@example.com", full_name="Alex Capacity")

    response = client.get(f"/api/v1/accounts/{account['id']}/users/search", params={"q": "Capacity"})

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["full_name"] == "Alex Capacity"


def test_account_users_do_not_expose_cognito_sub(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    add_account_user(
        db_session,
        account_id=account["id"],
        email="cognito-person@example.com",
        full_name="Cognito Person",
        cognito_sub="secret-sub",
    )

    response = client.get(f"/api/v1/accounts/{account['id']}/users/search", params={"q": "cognito-person"})

    assert response.status_code == 200
    result = response.json()[0]
    assert result["email"] == "cognito-person@example.com"
    assert "cognito_sub" not in result


def test_account_users_pagination_works(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    add_account_user(db_session, account_id=account["id"], email="page-a@example.com", full_name="Page A")
    add_account_user(db_session, account_id=account["id"], email="page-b@example.com", full_name="Page B")

    response = client.get(
        f"/api/v1/accounts/{account['id']}/users",
        params={"paginated": "true", "page": 1, "page_size": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total"] == 3
    assert len(body["items"]) == 2


def test_owner_can_invite_user(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Invite Owner", slug="invite-owner")

    response = invite_user(
        client,
        account["id"],
        email="New.User@Example.COM",
        full_name="New User",
        role="MEMBER",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "new.user@example.com"
    assert body["status"] == "CREATED"
    assert body["role"] == "MEMBER"
    assert body["full_name"] == "New User"
    assert body["account_id"] == account["id"]
    assert "cognito_sub" not in body

    user = db_session.scalar(select(User).where(User.email == "new.user@example.com"))
    assert user is not None
    membership = db_session.scalar(
        select(AccountMember).where(
            AccountMember.account_id == UUID(account["id"]),
            AccountMember.user_id == user.id,
        )
    )
    assert membership is not None
    assert membership.role == AccountMemberRole.MEMBER.value


def test_invite_rejects_invalid_email(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Invite Invalid Email", slug="invite-invalid-email")

    response = invite_user(client, account["id"], email="not-an-email")

    assert response.status_code == 422
    assert db_session.scalar(select(User).where(User.email == "not-an-email")) is None


def test_invite_rejects_invalid_role(client: TestClient) -> None:
    account = create_account(client, name="Invite Invalid Role", slug="invite-invalid-role")

    response = invite_user(client, account["id"], email="invalid-role@example.com", role="POWER_USER")

    assert response.status_code == 422


def test_invite_rejects_retired_admin_role(client: TestClient) -> None:
    account = create_account(client, name="Invite Admin Role", slug="invite-admin-role")

    response = invite_user(client, account["id"], email="admin-role@example.com", role="ADMIN")

    assert response.status_code == 422


def test_existing_user_can_be_added_to_account(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Invite Existing", slug="invite-existing")
    existing_user = User(email="existing@example.com", full_name="Existing Person")
    db_session.add(existing_user)
    db_session.commit()
    db_session.refresh(existing_user)

    response = invite_user(client, account["id"], email="EXISTING@example.com", role="MANAGER")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CREATED"
    assert body["user_id"] == str(existing_user.id)
    assert body["email"] == "existing@example.com"
    assert body["full_name"] == "Existing Person"
    membership = db_session.scalar(
        select(AccountMember).where(
            AccountMember.account_id == UUID(account["id"]),
            AccountMember.user_id == existing_user.id,
        )
    )
    assert membership is not None
    assert membership.role == AccountMemberRole.MANAGER.value


def test_duplicate_membership_does_not_create_duplicate(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Invite Duplicate", slug="invite-duplicate")
    first_response = invite_user(client, account["id"], email="duplicate@example.com", role="MEMBER")
    assert first_response.status_code == 200

    second_response = invite_user(client, account["id"], email="duplicate@example.com", role="OWNER")

    assert second_response.status_code == 200
    body = second_response.json()
    assert body["status"] == "ALREADY_EXISTS"
    assert body["role"] == "MEMBER"

    user = db_session.scalar(select(User).where(User.email == "duplicate@example.com"))
    assert user is not None
    memberships = list(
        db_session.scalars(
            select(AccountMember).where(
                AccountMember.account_id == UUID(account["id"]),
                AccountMember.user_id == user.id,
            )
        ).all()
    )
    assert len(memberships) == 1
    assert memberships[0].role == AccountMemberRole.MEMBER.value


def test_duplicate_membership_updates_role_when_requested(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Invite Update", slug="invite-update")
    first_response = invite_user(client, account["id"], email="update-role@example.com", role="MEMBER")
    assert first_response.status_code == 200

    second_response = invite_user(
        client,
        account["id"],
        email="update-role@example.com",
        role="OWNER",
        update_existing=True,
    )

    assert second_response.status_code == 200
    body = second_response.json()
    assert body["status"] == "UPDATED"
    assert body["role"] == "OWNER"
    user = db_session.scalar(select(User).where(User.email == "update-role@example.com"))
    assert user is not None
    membership = db_session.scalar(
        select(AccountMember).where(
            AccountMember.account_id == UUID(account["id"]),
            AccountMember.user_id == user.id,
        )
    )
    assert membership is not None
    assert membership.role == AccountMemberRole.OWNER.value


def test_bulk_invite_handles_mixed_success_failure(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Bulk Mixed", slug="bulk-mixed")

    response = client.post(
        f"/api/v1/accounts/{account['id']}/users/bulk-invite",
        json={
            "users": [
                {"email": "bulk-one@example.com", "full_name": "Bulk One", "role": "MEMBER"},
                {"email": "bad-email", "full_name": "Bad Email", "role": "MEMBER"},
                {"email": "admin-bulk@example.com", "full_name": "Admin Bulk", "role": "ADMIN"},
                {"email": "bulk-two@example.com", "full_name": "Bulk Two", "role": "VIEWER"},
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 2
    assert body["updated"] == 0
    assert body["already_exists"] == 0
    assert body["failed"] == 2
    assert [result["status"] for result in body["results"]] == ["CREATED", "FAILED", "FAILED", "CREATED"]
    assert db_session.scalar(select(User).where(User.email == "bulk-one@example.com")) is not None
    assert db_session.scalar(select(User).where(User.email == "bulk-two@example.com")) is not None
    assert db_session.scalar(select(User).where(User.email == "admin-bulk@example.com")) is None


def test_bulk_invite_does_not_fail_entire_request_for_one_bad_row(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Bulk Partial", slug="bulk-partial")

    response = client.post(
        f"/api/v1/accounts/{account['id']}/users/bulk-invite",
        json={
            "users": [
                {"email": "valid-row@example.com", "full_name": "Valid Row", "role": "MEMBER"},
                {"email": "invalid-row", "full_name": "Invalid Row", "role": "MEMBER"},
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["failed"] == 1
    assert body["results"][0]["status"] == "CREATED"
    assert body["results"][1]["status"] == "FAILED"
    assert db_session.scalar(select(User).where(User.email == "valid-row@example.com")) is not None


def test_cognito_invite_is_skipped_when_disabled(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = create_account(client, name="Cognito Disabled", slug="cognito-disabled")
    calls: list[str] = []

    def fake_admin_create_user_invite(**kwargs: object) -> bool:
        calls.append(str(kwargs["email"]))
        return True

    monkeypatch.setattr(account_user_service, "admin_create_user_invite", fake_admin_create_user_invite)

    response = invite_user(client, account["id"], email="disabled-cognito@example.com")

    assert response.status_code == 200
    assert calls == []


def test_cognito_invite_is_called_when_enabled_and_mocked(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = create_account(client, name="Cognito Enabled", slug="cognito-enabled")
    current_user = db_session.scalar(select(User).where(User.email == "dev@example.com"))
    assert current_user is not None
    calls: list[dict[str, object]] = []

    def override_get_settings() -> Settings:
        return Settings(
            auth_mode="cognito",
            cognito_user_pool_id="ca-central-1_example",
            cognito_app_client_id="client-id",
            cognito_invite_enabled=True,
        )

    def fake_admin_create_user_invite(**kwargs: object) -> bool:
        calls.append(kwargs)
        return True

    app.dependency_overrides[get_settings] = override_get_settings
    app.dependency_overrides[get_current_user] = lambda: current_user
    monkeypatch.setattr(account_user_service, "admin_create_user_invite", fake_admin_create_user_invite)

    response = invite_user(
        client,
        account["id"],
        email="enabled-cognito@example.com",
        full_name="Enabled Cognito",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CREATED"
    assert len(calls) == 1
    assert calls[0]["email"] == "enabled-cognito@example.com"
    assert calls[0]["full_name"] == "Enabled Cognito"
    assert isinstance(calls[0]["settings"], Settings)

def test_platform_admin_can_list_and_invite_in_an_account_they_are_not_in(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_private_account(db_session)

    listed = client.get(f"/api/v1/accounts/{account.id}/users")
    invited = invite_user(client, account.id, email="added-by-platform@example.com")

    assert listed.status_code == 200
    assert invited.status_code == 200
    assert invited.json()["status"] == "CREATED"


def test_regular_non_member_cannot_invite(client: TestClient, db_session: Session) -> None:
    account = create_private_account(db_session)
    make_current_user_regular(client, db_session)

    response = invite_user(client, account.id, email="nope@example.com")

    assert response.status_code == 403


def test_account_users_include_role_and_sign_in_state(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    invite_user(client, account["id"], email="pending@example.com", role="VIEWER")

    users = {user["email"]: user for user in client.get(f"/api/v1/accounts/{account['id']}/users").json()}

    assert users["pending@example.com"]["role"] == "VIEWER"
    assert users["pending@example.com"]["has_signed_in"] is False
    assert users["dev@example.com"]["role"] == "OWNER"


def test_last_owner_cannot_be_demoted(client: TestClient) -> None:
    account = create_account(client, name="Solo Owner", slug="solo-owner")

    response = invite_user(client, account["id"], email="dev@example.com", role="MEMBER", update_existing=True)

    assert response.status_code == 409
    assert response.json()["message"] == "An account needs at least one owner."


def test_owner_can_be_demoted_when_another_owner_remains(client: TestClient) -> None:
    account = create_account(client, name="Two Owners", slug="two-owners")
    assert invite_user(client, account["id"], email="second@example.com", role="OWNER").status_code == 200

    response = invite_user(client, account["id"], email="second@example.com", role="MEMBER", update_existing=True)

    assert response.status_code == 200
    assert response.json()["role"] == "MEMBER"


def test_bulk_invite_reports_last_owner_demotion_as_failed(client: TestClient) -> None:
    account = create_account(client, name="Bulk Owner", slug="bulk-owner")

    response = client.post(
        f"/api/v1/accounts/{account['id']}/users/bulk-invite",
        json={"users": [{"email": "dev@example.com", "role": "VIEWER"}], "update_existing": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == 1
    assert body["results"][0]["error"] == "An account needs at least one owner."


def test_owner_can_change_another_owners_role(client: TestClient) -> None:
    account = create_account(client, name="Role Change", slug="role-change")
    second = invite_user(client, account["id"], email="second@example.com", role="OWNER").json()

    response = client.patch(f"/api/v1/accounts/{account['id']}/users/{second['user_id']}", json={"role": "VIEWER"})

    assert response.status_code == 200
    assert response.json()["role"] == "VIEWER"
    users = {u["email"]: u["role"] for u in client.get(f"/api/v1/accounts/{account['id']}/users").json()}
    assert users["second@example.com"] == "VIEWER"


def test_role_change_cannot_remove_the_last_owner(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Last Owner", slug="last-owner")
    dev = db_session.scalar(select(User).where(User.email == "dev@example.com"))

    response = client.patch(f"/api/v1/accounts/{account['id']}/users/{dev.id}", json={"role": "MANAGER"})

    assert response.status_code == 409
    assert response.json()["message"] == "An account needs at least one owner."


def test_non_owner_cannot_change_roles(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Not Owner", slug="not-owner")
    target = invite_user(client, account["id"], email="target@example.com").json()
    invite_user(client, account["id"], email="other-owner@example.com", role="OWNER")
    dev = db_session.scalar(select(User).where(User.email == "dev@example.com"))
    membership = db_session.scalar(select(AccountMember).where(AccountMember.user_id == dev.id))
    membership.role = AccountMemberRole.MANAGER.value
    db_session.commit()
    make_current_user_regular(client, db_session)

    changed = client.patch(f"/api/v1/accounts/{account['id']}/users/{target['user_id']}", json={"role": "VIEWER"})
    removed = client.delete(f"/api/v1/accounts/{account['id']}/users/{target['user_id']}")

    assert changed.status_code == 403
    assert removed.status_code == 403


def test_owner_can_remove_a_user_and_their_grants(client: TestClient, db_session: Session) -> None:
    from app.models.access_grant import AccessGrant

    account = create_account(client, name="Remove", slug="remove")
    portfolio = client.post(f"/api/v1/accounts/{account['id']}/portfolios", json={"name": "Portfolio"}).json()
    member = invite_user(client, account["id"], email="leaver@example.com").json()
    granted = client.post(
        f"/api/v1/accounts/{account['id']}/access-grants",
        json={"user_id": member["user_id"], "portfolio_id": portfolio["id"], "role": "MEMBER", "modules": ["TASKS"]},
    )
    assert granted.status_code == 201

    response = client.delete(f"/api/v1/accounts/{account['id']}/users/{member['user_id']}")

    assert response.status_code == 204
    emails = {u["email"] for u in client.get(f"/api/v1/accounts/{account['id']}/users").json()}
    assert "leaver@example.com" not in emails
    assert db_session.scalar(select(AccessGrant).where(AccessGrant.user_id == UUID(member["user_id"]))) is None


def test_last_owner_cannot_be_removed(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Keep Owner", slug="keep-owner")
    dev = db_session.scalar(select(User).where(User.email == "dev@example.com"))

    response = client.delete(f"/api/v1/accounts/{account['id']}/users/{dev.id}")

    assert response.status_code == 409


def test_changing_role_of_a_non_member_is_404(client: TestClient, db_session: Session) -> None:
    account = create_account(client, name="Stranger", slug="stranger")
    stranger = User(email="stranger@example.com", full_name="Stranger")
    db_session.add(stranger)
    db_session.commit()

    response = client.patch(f"/api/v1/accounts/{account['id']}/users/{stranger.id}", json={"role": "VIEWER"})

    assert response.status_code == 404
