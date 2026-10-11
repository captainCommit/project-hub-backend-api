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
from app.models.account_settings import AccountSettings
from app.models.account_member import AccountMember, AccountMemberRole
from app.models.user import User
from app.services.auth import DEV_USER_EMAIL, DEV_USER_FULL_NAME


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


def test_get_me_creates_and_returns_dev_user(client: TestClient, db_session: Session) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == DEV_USER_EMAIL
    assert body["full_name"] == DEV_USER_FULL_NAME
    assert body["is_active"] is True

    user = db_session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    assert user is not None


def test_create_account_creates_owner_membership(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/api/v1/accounts",
        json={"name": "Acme", "slug": "acme"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Acme"
    assert body["slug"] == "acme"

    user = db_session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    assert user is not None

    account_id = UUID(body["id"])
    membership = db_session.scalar(
        select(AccountMember).where(
            AccountMember.account_id == account_id,
            AccountMember.user_id == user.id,
        )
    )
    assert membership is not None
    assert membership.role == AccountMemberRole.OWNER.value


def test_create_account_creates_default_settings(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/api/v1/accounts",
        json={"name": "Settings Account", "slug": "settings-account"},
    )

    assert response.status_code == 201
    account_id = UUID(response.json()["id"])
    settings = db_session.scalar(select(AccountSettings).where(AccountSettings.account_id == account_id))
    assert settings is not None
    assert settings.date_format == "MM/dd/yyyy"
    assert settings.default_landing_page == "PORTFOLIOS"
    assert settings.hide_delivery_section is False
    assert settings.non_working_weekdays == ["SATURDAY", "SUNDAY"]


def test_list_accounts_returns_only_current_user_memberships(
    client: TestClient,
    db_session: Session,
) -> None:
    owned_response = client.post(
        "/api/v1/accounts",
        json={"name": "Owned Account", "slug": "owned-account"},
    )
    assert owned_response.status_code == 201

    other_user = User(email="other@example.com", full_name="Other User")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(name="Other Account", slug="other-account", created_by=other_user.id)
    db_session.add(other_account)
    db_session.flush()
    db_session.add(
        AccountMember(
            account_id=other_account.id,
            user_id=other_user.id,
            role=AccountMemberRole.OWNER.value,
        )
    )
    db_session.commit()

    response = client.get("/api/v1/accounts")

    assert response.status_code == 200
    slugs = {account["slug"] for account in response.json()}
    assert slugs == {"owned-account"}


def test_get_account_blocks_non_members(client: TestClient, db_session: Session) -> None:
    other_user = User(email="other@example.com", full_name="Other User")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(name="Other Account", slug="other-account", created_by=other_user.id)
    db_session.add(other_account)
    db_session.flush()
    db_session.add(
        AccountMember(
            account_id=other_account.id,
            user_id=other_user.id,
            role=AccountMemberRole.OWNER.value,
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/accounts/{other_account.id}")

    assert response.status_code == 403
    assert response.json()["message"] == "Account access denied."


def test_patch_account_allows_owner(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "Original", "slug": "original"},
    )
    account_id = UUID(create_response.json()["id"])

    response = client.patch(
        f"/api/v1/accounts/{account_id}",
        json={"name": "Updated", "slug": "updated"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Updated"
    assert body["slug"] == "updated"


def test_patch_account_blocks_insufficient_role(
    client: TestClient,
    db_session: Session,
) -> None:
    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "Viewer Account", "slug": "viewer-account"},
    )
    account_id = UUID(create_response.json()["id"])

    membership = db_session.scalar(
        select(AccountMember).where(AccountMember.account_id == account_id)
    )
    assert membership is not None
    membership.role = AccountMemberRole.VIEWER.value
    db_session.commit()

    response = client.patch(
        f"/api/v1/accounts/{account_id}",
        json={"name": "Blocked Update"},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_member_can_read_account_settings(client: TestClient, db_session: Session) -> None:
    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "Read Settings", "slug": "read-settings"},
    )
    account_id = UUID(create_response.json()["id"])

    membership = db_session.scalar(select(AccountMember).where(AccountMember.account_id == account_id))
    assert membership is not None
    membership.role = AccountMemberRole.MEMBER.value
    db_session.commit()

    response = client.get(f"/api/v1/accounts/{account_id}/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == str(account_id)
    assert body["non_working_weekdays"] == ["SATURDAY", "SUNDAY"]


def test_get_account_settings_creates_default_when_missing(client: TestClient, db_session: Session) -> None:
    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "Missing Settings", "slug": "missing-settings"},
    )
    account_id = UUID(create_response.json()["id"])
    settings = db_session.scalar(select(AccountSettings).where(AccountSettings.account_id == account_id))
    assert settings is not None
    db_session.delete(settings)
    db_session.commit()

    response = client.get(f"/api/v1/accounts/{account_id}/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["date_format"] == "MM/dd/yyyy"
    assert body["default_landing_page"] == "PORTFOLIOS"
    assert body["non_working_weekdays"] == ["SATURDAY", "SUNDAY"]


def test_viewer_cannot_update_account_settings(client: TestClient, db_session: Session) -> None:
    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "Viewer Settings", "slug": "viewer-settings"},
    )
    account_id = UUID(create_response.json()["id"])

    membership = db_session.scalar(select(AccountMember).where(AccountMember.account_id == account_id))
    assert membership is not None
    membership.role = AccountMemberRole.VIEWER.value
    db_session.commit()

    response = client.patch(
        f"/api/v1/accounts/{account_id}/settings",
        json={"hide_delivery_section": True},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Insufficient account role."


def test_owner_and_admin_can_update_account_settings(client: TestClient, db_session: Session) -> None:
    owner_response = client.post(
        "/api/v1/accounts",
        json={"name": "Owner Settings", "slug": "owner-settings"},
    )
    owner_account_id = UUID(owner_response.json()["id"])

    owner_update_response = client.patch(
        f"/api/v1/accounts/{owner_account_id}/settings",
        json={
            "date_format": "yyyy-MM-dd",
            "default_landing_page": "FAVORITES",
            "hide_delivery_section": True,
            "non_working_weekdays": ["SUNDAY"],
        },
    )

    assert owner_update_response.status_code == 200
    owner_body = owner_update_response.json()
    assert owner_body["date_format"] == "yyyy-MM-dd"
    assert owner_body["default_landing_page"] == "FAVORITES"
    assert owner_body["hide_delivery_section"] is True
    assert owner_body["non_working_weekdays"] == ["SUNDAY"]

    admin_response = client.post(
        "/api/v1/accounts",
        json={"name": "Admin Settings", "slug": "admin-settings"},
    )
    admin_account_id = UUID(admin_response.json()["id"])
    membership = db_session.scalar(select(AccountMember).where(AccountMember.account_id == admin_account_id))
    assert membership is not None
    membership.role = AccountMemberRole.OWNER.value
    db_session.commit()

    admin_update_response = client.patch(
        f"/api/v1/accounts/{admin_account_id}/settings",
        json={"default_landing_page": "SPRINTS"},
    )

    assert admin_update_response.status_code == 200
    assert admin_update_response.json()["default_landing_page"] == "SPRINTS"


def test_account_settings_accept_day_month_name_date_format(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "Long Date Format", "slug": "long-date-format"},
    )
    account_id = UUID(create_response.json()["id"])

    response = client.patch(
        f"/api/v1/accounts/{account_id}/settings",
        json={"date_format": "D MMM YYYY"},
    )

    assert response.status_code == 200
    assert response.json()["date_format"] == "D MMM YYYY"


def test_invalid_account_settings_date_format_rejected(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "Invalid Date Format", "slug": "invalid-date-format"},
    )
    account_id = UUID(create_response.json()["id"])

    response = client.patch(
        f"/api/v1/accounts/{account_id}/settings",
        json={"date_format": "yyyy/MM/dd"},
    )

    assert response.status_code == 422


def test_invalid_account_settings_landing_page_rejected(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/accounts",
        json={"name": "Invalid Landing", "slug": "invalid-landing"},
    )
    account_id = UUID(create_response.json()["id"])

    response = client.patch(
        f"/api/v1/accounts/{account_id}/settings",
        json={"default_landing_page": "TASKS"},
    )

    assert response.status_code == 422


def test_get_missing_account_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/accounts/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["message"] == "Account not found."

def make_current_user_regular(db_session: Session) -> User:
    client_user = db_session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    assert client_user is not None
    client_user.is_platform_admin = False
    db_session.commit()
    return client_user


def test_me_reports_platform_admin(client: TestClient) -> None:
    assert client.get("/api/v1/me").json()["is_platform_admin"] is True


def test_only_platform_admins_can_create_accounts(client: TestClient, db_session: Session) -> None:
    client.get("/api/v1/me")
    make_current_user_regular(db_session)

    response = client.post("/api/v1/accounts", json={"name": "Nope", "slug": "nope"})

    assert response.status_code == 403
    assert response.json()["message"] == "Platform admin access required."
    assert db_session.scalar(select(Account).where(Account.slug == "nope")) is None


def test_create_account_with_admin_email_invites_that_admin(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/api/v1/accounts",
        json={"name": "Client Co", "slug": "client-co", "admin_email": " Boss@Client.example ", "admin_full_name": "Boss"},
    )

    assert response.status_code == 201
    account_id = UUID(response.json()["id"])
    members = db_session.execute(
        select(User.email, User.full_name, AccountMember.role)
        .join(AccountMember, AccountMember.user_id == User.id)
        .where(AccountMember.account_id == account_id)
    ).all()
    # The creating platform admin is not added; the named person is the admin.
    assert members == [("boss@client.example", "Boss", AccountMemberRole.OWNER.value)]
    assert db_session.scalar(select(AccountSettings).where(AccountSettings.account_id == account_id)) is not None


def test_create_account_rejects_invalid_admin_email(client: TestClient) -> None:
    response = client.post("/api/v1/accounts", json={"name": "Bad", "slug": "bad", "admin_email": "not-an-email"})

    assert response.status_code == 422


def test_list_accounts_includes_current_user_role(client: TestClient) -> None:
    client.post("/api/v1/accounts", json={"name": "Mine", "slug": "mine"})

    response = client.get("/api/v1/accounts")

    assert [(account["slug"], account["role"]) for account in response.json()] == [("mine", "OWNER")]


def test_platform_admin_lists_every_account_with_its_admins(client: TestClient, db_session: Session) -> None:
    client.post("/api/v1/accounts", json={"name": "Beta", "slug": "beta", "admin_email": "admin@beta.example"})
    client.post("/api/v1/accounts", json={"name": "Alpha", "slug": "alpha"})

    response = client.get("/api/v1/admin/accounts")

    assert response.status_code == 200
    body = response.json()
    assert [account["slug"] for account in body] == ["alpha", "beta"]
    assert body[0]["member_count"] == 1
    assert [(a["email"], a["role"]) for a in body[0]["admins"]] == [(DEV_USER_EMAIL, "OWNER")]
    assert [(a["email"], a["role"]) for a in body[1]["admins"]] == [("admin@beta.example", "OWNER")]


def test_regular_users_cannot_list_every_account(client: TestClient, db_session: Session) -> None:
    client.get("/api/v1/me")
    make_current_user_regular(db_session)

    assert client.get("/api/v1/admin/accounts").status_code == 403


def add_member(db_session: Session, account_id: UUID, email: str, role: AccountMemberRole, *, signed_in: bool = True) -> User:
    user = User(email=email, cognito_sub=f"sub-{email}" if signed_in else None)
    db_session.add(user)
    db_session.flush()
    db_session.add(AccountMember(account_id=account_id, user_id=user.id, role=role.value))
    db_session.commit()
    return user


def test_billing_counts_signed_in_members_and_prices_viewers_at_half(client: TestClient, db_session: Session) -> None:
    account_id = UUID(client.post("/api/v1/accounts", json={"name": "Acme", "slug": "acme", "admin_email": "a@acme.example"}).json()["id"])
    # The invited admin has not signed in yet, so holds no seat.
    add_member(db_session, account_id, "m1@acme.example", AccountMemberRole.MANAGER)
    add_member(db_session, account_id, "m2@acme.example", AccountMemberRole.MEMBER)
    add_member(db_session, account_id, "v1@acme.example", AccountMemberRole.VIEWER)
    add_member(db_session, account_id, "v2@acme.example", AccountMemberRole.VIEWER)
    add_member(db_session, account_id, "p@acme.example", AccountMemberRole.MEMBER, signed_in=False)
    inactive = add_member(db_session, account_id, "gone@acme.example", AccountMemberRole.MEMBER)
    inactive.is_active = False
    db_session.commit()

    response = client.get("/api/v1/admin/billing")

    assert response.status_code == 200
    body = response.json()
    assert (body["currency"], body["seat_price_cents"], body["viewer_seat_price_cents"]) == ("CAD", 2000, 1000)
    [line] = body["accounts"]
    assert line["seats"] == {"OWNER": 0, "MANAGER": 1, "MEMBER": 1, "VIEWER": 2}
    assert (line["full_seats"], line["viewer_seats"], line["pending_invites"]) == (2, 2, 2)
    assert line["monthly_total_cents"] == 2 * 2000 + 2 * 1000
    assert body["monthly_total_cents"] == line["monthly_total_cents"]


def test_billing_seat_counts_cover_every_role() -> None:
    from app.schemas.platform import SeatCounts

    assert set(SeatCounts.model_fields) == {role.value for role in AccountMemberRole}


def test_system_status_reports_database_and_counts(client: TestClient) -> None:
    client.post("/api/v1/accounts", json={"name": "Acme", "slug": "acme"})

    response = client.get("/api/v1/admin/system")

    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "connected"
    assert body["migration_version"] is None
    assert (body["account_count"], body["user_count"]) == (1, 1)


def test_regular_users_cannot_see_billing_or_system(client: TestClient, db_session: Session) -> None:
    client.get("/api/v1/me")
    make_current_user_regular(db_session)

    assert client.get("/api/v1/admin/billing").status_code == 403
    assert client.get("/api/v1/admin/system").status_code == 403


def audit_actions(client: TestClient, **query: str) -> list[tuple[str, str | None, str | None]]:
    response = client.get("/api/v1/admin/audit", params=query)
    assert response.status_code == 200
    return [(e["action"], e["account_name"], e["target"]) for e in response.json()["items"]]


def test_platform_admin_actions_on_other_accounts_are_audited(client: TestClient) -> None:
    account_id = client.post(
        "/api/v1/accounts", json={"name": "Acme", "slug": "acme", "admin_email": "boss@acme.example"}
    ).json()["id"]

    assert client.get(f"/api/v1/accounts/{account_id}/users").status_code == 200
    invite = {"email": "new@acme.example", "role": "MEMBER"}
    assert client.post(f"/api/v1/accounts/{account_id}/users/invite", json=invite).status_code == 200
    promote = {"email": "new@acme.example", "role": "MANAGER", "update_existing": True}
    assert client.post(f"/api/v1/accounts/{account_id}/users/invite", json=promote).status_code == 200
    bulk = {"users": [{"email": "v@acme.example", "role": "VIEWER"}]}
    assert client.post(f"/api/v1/accounts/{account_id}/users/bulk-invite", json=bulk).status_code == 200

    assert audit_actions(client) == [
        ("account.users_bulk_invited", "Acme", None),
        ("account.user_role_changed", "Acme", "new@acme.example"),
        ("account.user_invited", "Acme", "new@acme.example"),
        ("account.users_viewed", "Acme", None),
        ("account.created", "Acme", "boss@acme.example"),
    ]
    assert len(audit_actions(client, account_id=account_id)) == 5


def test_platform_admin_working_in_own_account_is_not_audited(client: TestClient) -> None:
    account_id = client.post("/api/v1/accounts", json={"name": "Mine", "slug": "mine"}).json()["id"]

    client.get(f"/api/v1/accounts/{account_id}/users")
    client.post(f"/api/v1/accounts/{account_id}/users/invite", json={"email": "x@mine.example", "role": "MEMBER"})

    assert audit_actions(client) == [("account.created", "Mine", None)]


def test_regular_users_cannot_read_audit_log(client: TestClient, db_session: Session) -> None:
    client.get("/api/v1/me")
    make_current_user_regular(db_session)

    assert client.get("/api/v1/admin/audit").status_code == 403
