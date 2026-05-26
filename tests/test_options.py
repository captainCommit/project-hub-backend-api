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
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.user import User
from app.services.auth import DEV_USER_EMAIL
from app.services.option_defaults import DEFAULT_OPTION_SETS


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


def get_dev_user(db_session: Session) -> User:
    user = db_session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    assert user is not None
    return user


def set_current_user_role(db_session: Session, account_id: str | UUID, role: AccountMemberRole) -> None:
    membership = db_session.scalar(
        select(AccountMember).where(AccountMember.account_id == UUID(str(account_id)))
    )
    assert membership is not None
    membership.role = role.value
    db_session.commit()


def get_task_status_option_set(db_session: Session, account_id: str | UUID) -> OptionSet:
    option_set = db_session.scalar(
        select(OptionSet).where(
            OptionSet.account_id == UUID(str(account_id)),
            OptionSet.entity_type == "TASK",
            OptionSet.name == "STATUS",
        )
    )
    assert option_set is not None
    return option_set


def test_creating_account_seeds_default_option_sets_and_values(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_account(client)
    account_id = UUID(account["id"])

    option_sets = db_session.scalars(
        select(OptionSet).where(OptionSet.account_id == account_id)
    ).all()
    option_values = db_session.scalars(
        select(OptionValue).join(OptionSet, OptionValue.option_set_id == OptionSet.id)
    ).all()

    expected_value_count = sum(len(tuple(definition["values"])) for definition in DEFAULT_OPTION_SETS)
    assert len(option_sets) == len(DEFAULT_OPTION_SETS)
    assert len(option_values) == expected_value_count

    task_status = get_task_status_option_set(db_session, account_id)
    task_status_values = db_session.scalars(
        select(OptionValue)
        .where(OptionValue.option_set_id == task_status.id)
        .order_by(OptionValue.sort_order)
    ).all()
    assert [value.value for value in task_status_values] == [
        "NOT_STARTED",
        "IN_PROGRESS",
        "BLOCKED",
        "COMPLETE",
    ]


def test_account_member_can_read_options(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    set_current_user_role(db_session, account["id"], AccountMemberRole.VIEWER)

    response = client.get(f"/api/v1/accounts/{account['id']}/options?entityType=task&name=status")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["entity_type"] == "TASK"
    assert body[0]["name"] == "STATUS"
    assert {value["value"] for value in body[0]["values"]} == {
        "NOT_STARTED",
        "IN_PROGRESS",
        "BLOCKED",
        "COMPLETE",
    }


def test_viewer_cannot_create_option_values(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    option_set = get_task_status_option_set(db_session, account["id"])
    set_current_user_role(db_session, account["id"], AccountMemberRole.VIEWER)

    response = client.post(
        f"/api/v1/option-sets/{option_set.id}/values",
        json={"label": "Ready for Review"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient account role."}


def test_owner_can_create_option_values(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    option_set = get_task_status_option_set(db_session, account["id"])

    response = client.post(
        f"/api/v1/option-sets/{option_set.id}/values",
        json={"label": "Ready for Review", "color": "#ffaa00", "sort_order": 10},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["label"] == "Ready for Review"
    assert body["value"] == "READY_FOR_REVIEW"
    assert body["color"] == "#ffaa00"
    assert body["sort_order"] == 10


def test_delete_option_value_sets_is_active_false(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    option_set = get_task_status_option_set(db_session, account["id"])
    option_value = db_session.scalar(
        select(OptionValue).where(
            OptionValue.option_set_id == option_set.id,
            OptionValue.value == "BLOCKED",
        )
    )
    assert option_value is not None

    response = client.delete(f"/api/v1/option-values/{option_value.id}")

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    db_session.refresh(option_value)
    assert option_value.is_active is False


def test_include_inactive_false_excludes_inactive_values(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_account(client)
    option_set = get_task_status_option_set(db_session, account["id"])
    option_value = db_session.scalar(
        select(OptionValue).where(
            OptionValue.option_set_id == option_set.id,
            OptionValue.value == "BLOCKED",
        )
    )
    assert option_value is not None
    client.delete(f"/api/v1/option-values/{option_value.id}")

    response = client.get(f"/api/v1/option-sets/{option_set.id}/values")

    assert response.status_code == 200
    assert "BLOCKED" not in {value["value"] for value in response.json()}


def test_include_inactive_true_includes_inactive_values_for_owner(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_account(client)
    option_set = get_task_status_option_set(db_session, account["id"])
    option_value = db_session.scalar(
        select(OptionValue).where(
            OptionValue.option_set_id == option_set.id,
            OptionValue.value == "BLOCKED",
        )
    )
    assert option_value is not None
    client.delete(f"/api/v1/option-values/{option_value.id}")

    response = client.get(f"/api/v1/option-sets/{option_set.id}/values?includeInactive=true")

    assert response.status_code == 200
    inactive_values = [value for value in response.json() if value["value"] == "BLOCKED"]
    assert inactive_values
    assert inactive_values[0]["is_active"] is False


def test_setting_one_default_unsets_other_defaults(
    client: TestClient,
    db_session: Session,
) -> None:
    account = create_account(client)
    option_set = get_task_status_option_set(db_session, account["id"])
    complete_value = db_session.scalar(
        select(OptionValue).where(
            OptionValue.option_set_id == option_set.id,
            OptionValue.value == "COMPLETE",
        )
    )
    assert complete_value is not None

    response = client.patch(
        f"/api/v1/option-values/{complete_value.id}",
        json={"is_default": True},
    )

    assert response.status_code == 200
    values = db_session.scalars(select(OptionValue).where(OptionValue.option_set_id == option_set.id)).all()
    defaults = [value for value in values if value.is_default]
    assert len(defaults) == 1
    assert defaults[0].value == "COMPLETE"


def test_non_member_cannot_read_options(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    other_user = User(email="other-options@example.com", full_name="Other Options User")
    db_session.add(other_user)
    db_session.flush()
    other_account = Account(name="Other", slug="other", created_by=other_user.id)
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

    response = client.get(f"/api/v1/accounts/{other_account.id}/options")

    assert response.status_code == 403
    assert response.json() == {"detail": "Account access denied."}

    # Sanity check that the current user's own account remains readable.
    own_response = client.get(f"/api/v1/accounts/{account['id']}/options")
    assert own_response.status_code == 200


def test_option_set_delete_is_not_defined(client: TestClient, db_session: Session) -> None:
    account = create_account(client)
    option_set = get_task_status_option_set(db_session, account["id"])
    response = client.delete(f"/api/v1/option-sets/{option_set.id}")

    assert response.status_code == 501