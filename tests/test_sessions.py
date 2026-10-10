"""Cookie-based browser sessions and authentication error handling."""

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import app
from app.models.user import User
from app.services import auth as auth_service
from app.services import sessions as session_service
from tests.helpers import assert_error_response
from tests.test_auth import db_session, make_client, make_signed_token  # noqa: F401


CSRF = {"X-Requested-With": "XMLHttpRequest"}
SETTINGS = Settings(
    _env_file=None,
    auth_mode="cognito",
    environment="dev",
    cognito_user_pool_id="ca-central-1_example",
    cognito_app_client_id="web-client",
    cognito_app_client_secret="shh",
    cognito_domain="https://login.example.com",
)


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:  # noqa: F811
    test_client = make_client(db_session, SETTINGS)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


def claims_for(**overrides: Any) -> dict[str, Any]:
    return {"sub": "sub-1", "email": "jane@example.com", "name": "Jane", "token_use": "id", **overrides}


@pytest.fixture()
def valid_token(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Any token string 'tok:<sub>' validates; tests may tweak the claims."""
    claims = claims_for()
    monkeypatch.setattr(auth_service, "validate_cognito_token", lambda token, settings, access_token=None: dict(claims))
    monkeypatch.setattr(session_service, "validate_cognito_token", lambda token, settings, access_token=None: dict(claims))
    return claims


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.ok = status_code < 400
        self.text = str(self._payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class CognitoCalls(list):
    """Recorded calls to Cognito; queue fake responses in .responses."""

    def __init__(self) -> None:
        super().__init__()
        self.responses: list[FakeResponse] = []


@pytest.fixture()
def cognito(monkeypatch: pytest.MonkeyPatch) -> CognitoCalls:
    calls = CognitoCalls()

    def fake_post(url: str, data: dict[str, str], auth: tuple[str, str], **_: object) -> FakeResponse:
        assert auth == ("web-client", "shh")
        calls.append((url, data))
        return calls.responses.pop(0) if calls.responses else FakeResponse(200, {})

    monkeypatch.setattr(session_service.requests, "post", fake_post)
    return calls


TOKENS = {"id_token": "tok", "access_token": "acc", "refresh_token": "ref", "expires_in": 3600}


def test_session_exchange_sets_strict_httponly_cookies(client: TestClient, valid_token: dict, cognito: CognitoCalls) -> None:
    cognito.responses.append(FakeResponse(200, TOKENS))

    response = client.post(
        "/api/v1/auth/session",
        json={"code": "abc", "code_verifier": "v" * 43, "redirect_uri": "http://localhost:5173/auth/callback"},
        headers=CSRF,
    )

    assert response.status_code == 200, response.json()
    assert response.json()["email"] == "jane@example.com"
    assert cognito[0][0] == "https://login.example.com/oauth2/token"
    assert cognito[0][1]["grant_type"] == "authorization_code"
    assert cognito[0][1]["code_verifier"] == "v" * 43
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("ph_session="))
    refresh_cookie = next(c for c in cookies if c.startswith("ph_refresh="))
    for cookie in (session_cookie, refresh_cookie):
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert "Path=/api;" in session_cookie or session_cookie.endswith("Path=/api")
    assert "Path=/api/v1/auth" in refresh_cookie


def test_session_exchange_requires_csrf_header(client: TestClient, valid_token: dict, cognito: CognitoCalls) -> None:
    response = client.post(
        "/api/v1/auth/session",
        json={"code": "abc", "code_verifier": "v" * 43, "redirect_uri": "http://localhost:5173/auth/callback"},
    )

    assert_error_response(response, status_code=403, error_code="CSRF_CHECK_FAILED", message="Missing X-Requested-With header.")
    assert cognito == []


@pytest.mark.parametrize(
    ("cognito_status", "status_code", "error_code"),
    ((400, 401, "LOGIN_FAILED"), (500, 503, "AUTH_UNAVAILABLE")),
)
def test_session_exchange_errors(
    client: TestClient,
    valid_token: dict,
    cognito: CognitoCalls,
    cognito_status: int,
    status_code: int,
    error_code: str,
) -> None:
    cognito.responses.append(FakeResponse(cognito_status, {"error": "invalid_grant"}))

    response = client.post(
        "/api/v1/auth/session",
        json={"code": "abc", "code_verifier": "v" * 43, "redirect_uri": "http://localhost:5173/auth/callback"},
        headers=CSRF,
    )

    assert response.status_code == status_code
    assert response.json()["error_code"] == error_code


def test_cognito_unreachable_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object, **__: object) -> None:
        raise requests.ConnectionError("down")

    monkeypatch.setattr(session_service.requests, "post", boom)

    response = client.post(
        "/api/v1/auth/session",
        json={"code": "abc", "code_verifier": "v" * 43, "redirect_uri": "http://localhost:5173/auth/callback"},
        headers=CSRF,
    )

    assert response.status_code == 503
    assert response.json()["error_code"] == "AUTH_UNAVAILABLE"


def test_session_not_configured(db_session: Session) -> None:  # noqa: F811
    client = make_client(db_session, Settings(_env_file=None, auth_mode="cognito", environment="dev"))
    try:
        response = client.post(
            "/api/v1/auth/session",
            json={"code": "abc", "code_verifier": "v" * 43, "redirect_uri": "http://x/auth/callback"},
            headers=CSRF,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error_code"] == "AUTH_NOT_CONFIGURED"


def test_cookie_session_authenticates_and_guards_writes(client: TestClient, valid_token: dict) -> None:
    client.cookies.set("ph_session", "tok", path="/api")

    read = client.get("/api/v1/me")
    write_without_header = client.patch("/api/v1/me", json={"title": "PM"})
    write_with_header = client.patch("/api/v1/me", json={"title": "PM"}, headers=CSRF)

    assert read.status_code == 200
    assert write_without_header.status_code == 403
    assert write_without_header.json()["error_code"] == "CSRF_CHECK_FAILED"
    assert write_with_header.status_code == 200


def test_bearer_writes_do_not_need_csrf_header(client: TestClient, valid_token: dict) -> None:
    response = client.patch("/api/v1/me", json={"title": "PM"}, headers={"Authorization": "Bearer tok"})

    assert response.status_code == 200


def test_no_credentials_is_auth_required(client: TestClient) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTH_REQUIRED"


def test_expired_token_is_reported_as_expired(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    token, jwks = make_signed_token(
        {
            **claims_for(aud="web-client"),
            "iss": SETTINGS.cognito_issuer,
            "exp": int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp()),
        }
    )
    monkeypatch.setattr(auth_service, "get_cognito_jwks", lambda settings: jwks)

    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["error_code"] == "TOKEN_EXPIRED"


def test_jwks_unreachable_is_service_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    token, _ = make_signed_token({**claims_for(aud="web-client"), "iss": SETTINGS.cognito_issuer})

    def boom(*_: object, **__: object) -> None:
        raise requests.ConnectionError("down")

    auth_service._jwks_cache.clear()
    monkeypatch.setattr(auth_service.requests, "get", boom)

    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    assert response.json()["error_code"] == "AUTH_UNAVAILABLE"


def test_disabled_user_is_rejected(client: TestClient, db_session: Session, valid_token: dict) -> None:  # noqa: F811
    db_session.add(User(email="jane@example.com", cognito_sub="sub-1", is_active=False))
    db_session.commit()

    response = client.get("/api/v1/me", headers={"Authorization": "Bearer tok"})

    assert response.status_code == 403
    assert response.json()["error_code"] == "USER_DISABLED"


def test_invited_user_is_linked_by_email(client: TestClient, db_session: Session, valid_token: dict) -> None:  # noqa: F811
    db_session.add(User(email="Jane@Example.com", cognito_sub=None, full_name="Jane Invited"))
    db_session.commit()

    response = client.get("/api/v1/me", headers={"Authorization": "Bearer tok"})

    assert response.status_code == 200
    assert response.json()["cognito_sub"] == "sub-1"
    assert db_session.query(User).count() == 1


@pytest.mark.parametrize(("email_verified", "status_code"), ((True, 200), (False, 409)))
def test_pool_migration_relinks_only_verified_email(
    client: TestClient,
    db_session: Session,  # noqa: F811
    valid_token: dict,
    email_verified: bool,
    status_code: int,
) -> None:
    valid_token["email_verified"] = email_verified
    db_session.add(User(email="jane@example.com", cognito_sub="old-pool-sub"))
    db_session.commit()

    response = client.get("/api/v1/me", headers={"Authorization": "Bearer tok"})

    assert response.status_code == status_code
    if status_code == 409:
        assert response.json()["error_code"] == "ACCOUNT_CONFLICT"


def test_missing_name_claim_keeps_profile_name(client: TestClient, db_session: Session, valid_token: dict) -> None:  # noqa: F811
    valid_token.pop("name")
    db_session.add(User(email="jane@example.com", cognito_sub="sub-1", full_name="Jane Edited"))
    db_session.commit()

    response = client.get("/api/v1/me", headers={"Authorization": "Bearer tok"})

    assert response.json()["full_name"] == "Jane Edited"


def test_refresh_without_cookie_is_session_expired(client: TestClient) -> None:
    response = client.post("/api/v1/auth/refresh", headers=CSRF)

    assert response.status_code == 401
    assert response.json()["error_code"] == "SESSION_EXPIRED"


def test_refresh_renews_session_cookie(client: TestClient, valid_token: dict, cognito: CognitoCalls) -> None:
    cognito.responses.append(FakeResponse(200, {"id_token": "tok2", "access_token": "acc", "expires_in": 3600}))
    client.cookies.set("ph_refresh", "ref", path="/api/v1/auth")

    response = client.post("/api/v1/auth/refresh", headers=CSRF)

    assert response.status_code == 204
    assert cognito[0][1] == {"grant_type": "refresh_token", "refresh_token": "ref"}
    assert any(c.startswith("ph_session=tok2") for c in response.headers.get_list("set-cookie"))


def test_logout_revokes_and_clears_cookies(client: TestClient, cognito: CognitoCalls) -> None:
    client.cookies.set("ph_refresh", "ref", path="/api/v1/auth")

    response = client.post("/api/v1/auth/logout", headers=CSRF)

    assert response.status_code == 204
    assert cognito[0] == ("https://login.example.com/oauth2/revoke", {"token": "ref"})
    cleared = response.headers.get_list("set-cookie")
    assert any(c.startswith('ph_session=""') or c.startswith("ph_session=;") for c in cleared)
    assert any(c.startswith('ph_refresh=""') or c.startswith("ph_refresh=;") for c in cleared)


def test_hosted_login_id_tokens_with_at_hash_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: Cognito hosted-login ID tokens include at_hash; they must validate."""
    from jose.utils import base64url_encode
    import hashlib

    access_token = "access-token-value"
    digest = hashlib.sha256(access_token.encode()).digest()
    at_hash = base64url_encode(digest[: len(digest) // 2]).decode()
    token, jwks = make_signed_token(
        {
            **claims_for(aud="web-client"),
            "iss": SETTINGS.cognito_issuer,
            "at_hash": at_hash,
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
        }
    )
    monkeypatch.setattr(auth_service, "get_cognito_jwks", lambda settings: jwks)

    assert auth_service.validate_cognito_token(token, SETTINGS)["sub"] == "sub-1"
    assert auth_service.validate_cognito_token(token, SETTINGS, access_token)["sub"] == "sub-1"
    with pytest.raises(Exception) as excinfo:
        auth_service.validate_cognito_token(token, SETTINGS, "some-other-access-token")
    assert getattr(excinfo.value, "error_code", None) == "TOKEN_INVALID"
