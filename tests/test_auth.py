from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User
from app.services import auth as auth_service
from app.services.auth import DEV_USER_EMAIL, DEV_USER_FULL_NAME
from tests.helpers import assert_error_response


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


def make_client(db_session: Session, settings: Settings) -> TestClient:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def override_get_settings() -> Settings:
        return settings

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = override_get_settings
    return TestClient(app)


def test_missing_token_returns_401_in_cognito_mode(db_session: Session) -> None:
    client = make_client(
        db_session,
        Settings(
            auth_mode="cognito",
            cognito_user_pool_id="ca-central-1_example",
            cognito_app_client_id="client-id",
        ),
    )

    try:
        response = client.get("/api/v1/me")
    finally:
        app.dependency_overrides.clear()

    assert_error_response(
        response,
        status_code=401,
        error_code="AUTH_REQUIRED",
        message="Sign in to continue.",
    )


def test_local_mode_still_creates_and_returns_dev_user(db_session: Session) -> None:
    client = make_client(db_session, Settings(auth_mode="local"))

    try:
        response = client.get("/api/v1/me")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == DEV_USER_EMAIL
    assert body["full_name"] == DEV_USER_FULL_NAME

    user = db_session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    assert user is not None


def test_local_mode_is_blocked_outside_local_environment(db_session: Session) -> None:
    client = make_client(db_session, Settings(environment="production", auth_mode="local"))

    try:
        response = client.get("/api/v1/me")
    finally:
        app.dependency_overrides.clear()

    assert_error_response(
        response,
        status_code=401,
        error_code="UNAUTHORIZED",
        message="Local auth mode is only allowed when ENVIRONMENT=local.",
    )


def test_cognito_mode_syncs_user_from_mocked_valid_token(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_validate_cognito_token(token: str, settings: Settings) -> dict[str, str]:
        assert token == "valid-token"
        return {
            "sub": "cognito-user-sub",
            "email": "cognito@example.com",
            "name": "Cognito User",
            "token_use": "id",
        }

    monkeypatch.setattr(auth_service, "validate_cognito_token", fake_validate_cognito_token)

    client = make_client(
        db_session,
        Settings(
            auth_mode="cognito",
            cognito_user_pool_id="ca-central-1_example",
            cognito_app_client_id="client-id",
        ),
    )

    try:
        response = client.get("/api/v1/me", headers={"Authorization": "Bearer valid-token"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["cognito_sub"] == "cognito-user-sub"
    assert body["email"] == "cognito@example.com"
    assert body["full_name"] == "Cognito User"

    user = db_session.scalar(select(User).where(User.cognito_sub == "cognito-user-sub"))
    assert user is not None
    assert user.email == "cognito@example.com"

def make_signed_token(claims: dict[str, object]) -> tuple[str, dict[str, object]]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jose import jwk, jwt as jose_jwt

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_jwk = jwk.construct(private_key.public_key(), algorithm="RS256").to_dict()
    public_jwk["kid"] = "test-kid"
    token = jose_jwt.encode(claims, pem, algorithm="RS256", headers={"kid": "test-kid"})
    return token, {"keys": [public_jwk]}


@pytest.mark.parametrize(
    ("token_use", "client_claim", "client_id", "accepted"),
    (
        ("id", "aud", "main-client", True),
        ("id", "aud", "ui-client", True),
        ("access", "client_id", "ui-client", True),
        ("id", "aud", "unknown-client", False),
        ("access", "client_id", "unknown-client", False),
    ),
)
def test_cognito_validation_accepts_configured_client_ids(
    monkeypatch: pytest.MonkeyPatch,
    token_use: str,
    client_claim: str,
    client_id: str,
    accepted: bool,
) -> None:
    from datetime import datetime, timedelta, timezone

    from fastapi import HTTPException

    settings = Settings(
        auth_mode="cognito",
        cognito_user_pool_id="ca-central-1_example",
        cognito_app_client_id="main-client",
        cognito_additional_client_ids="ui-client, other-client",
    )
    token, jwks = make_signed_token(
        {
            "sub": "user-sub",
            "email": "user@example.com",
            "iss": settings.cognito_issuer,
            "token_use": token_use,
            client_claim: client_id,
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
        }
    )
    monkeypatch.setattr(auth_service, "get_cognito_jwks", lambda settings: jwks)

    if accepted:
        assert auth_service.validate_cognito_token(token, settings)["sub"] == "user-sub"
    else:
        with pytest.raises(HTTPException) as excinfo:
            auth_service.validate_cognito_token(token, settings)
        assert excinfo.value.status_code == 401
