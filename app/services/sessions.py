"""Browser sessions backed by Cognito, kept in HttpOnly SameSite=Strict cookies.

The browser never sees a token: it hands the API the authorization code (plus its
PKCE verifier) and the API exchanges it with the app client secret.
"""

import logging
from typing import Any

import requests
from fastapi import Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppHTTPException
from app.models.user import User
from app.services.auth import (
    REFRESH_COOKIE,
    SESSION_COOKIE,
    AuthErrorCode,
    sync_cognito_user,
    validate_cognito_token,
)


logger = logging.getLogger(__name__)

SESSION_COOKIE_PATH = "/api"
REFRESH_COOKIE_PATH = "/api/v1/auth"


class SessionErrorCode:
    LOGIN_FAILED = "LOGIN_FAILED"
    SESSION_EXPIRED = "SESSION_EXPIRED"


def require_hosted_login(settings: Settings) -> None:
    if not (settings.cognito_domain and settings.cognito_app_client_id and settings.cognito_app_client_secret):
        raise AppHTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            AuthErrorCode.AUTH_NOT_CONFIGURED,
            "Sign-in is not configured on the server.",
        )


def cognito_post(settings: Settings, path: str, data: dict[str, str]) -> requests.Response:
    try:
        return requests.post(
            f"{settings.cognito_domain.rstrip('/')}{path}",
            data=data,
            auth=(settings.cognito_app_client_id, settings.cognito_app_client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise AppHTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            AuthErrorCode.AUTH_UNAVAILABLE,
            "The sign-in service is unavailable. Please try again shortly.",
        ) from exc


def request_tokens(settings: Settings, data: dict[str, str], *, error_code: str, message: str) -> dict[str, Any]:
    response = cognito_post(settings, "/oauth2/token", data)
    if response.status_code >= 500:
        raise AppHTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            AuthErrorCode.AUTH_UNAVAILABLE,
            "The sign-in service is unavailable. Please try again shortly.",
        )
    if not response.ok:
        # invalid_grant: code reused/expired, PKCE mismatch, or refresh token revoked.
        logger.warning("Cognito token request rejected (%s): %s", response.status_code, response.text[:200])
        raise AppHTTPException(status.HTTP_401_UNAUTHORIZED, error_code, message)
    return response.json()


def set_session_cookies(response: Response, settings: Settings, tokens: dict[str, Any]) -> None:
    common = {"httponly": True, "secure": settings.session_cookie_secure, "samesite": "strict"}
    response.set_cookie(
        SESSION_COOKIE,
        tokens["id_token"],
        max_age=int(tokens.get("expires_in", 3600)),
        path=SESSION_COOKIE_PATH,
        **common,
    )
    if tokens.get("refresh_token"):
        response.set_cookie(
            REFRESH_COOKIE,
            tokens["refresh_token"],
            max_age=60 * 60 * 24 * 5,
            path=REFRESH_COOKIE_PATH,
            **common,
        )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    common = {"httponly": True, "secure": settings.session_cookie_secure, "samesite": "strict"}
    response.delete_cookie(SESSION_COOKIE, path=SESSION_COOKIE_PATH, **common)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, **common)


def start_session(
    *,
    db: Session,
    settings: Settings,
    response: Response,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> User:
    require_hosted_login(settings)
    tokens = request_tokens(
        settings,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        },
        error_code=SessionErrorCode.LOGIN_FAILED,
        message="This sign-in link is invalid or has expired. Please sign in again.",
    )
    user = sync_cognito_user(db, validate_cognito_token(tokens["id_token"], settings, tokens.get("access_token")))
    set_session_cookies(response, settings, tokens)
    return user


def refresh_session(*, settings: Settings, response: Response, refresh_token: str | None) -> None:
    require_hosted_login(settings)
    if not refresh_token:
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            SessionErrorCode.SESSION_EXPIRED,
            "Your session has ended. Please sign in again.",
        )
    tokens = request_tokens(
        settings,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        error_code=SessionErrorCode.SESSION_EXPIRED,
        message="Your session has ended. Please sign in again.",
    )
    validate_cognito_token(tokens["id_token"], settings, tokens.get("access_token"))
    set_session_cookies(response, settings, tokens)


def end_session(*, settings: Settings, response: Response, refresh_token: str | None) -> None:
    if refresh_token and settings.cognito_domain and settings.cognito_app_client_secret:
        try:
            cognito_post(settings, "/oauth2/revoke", {"token": refresh_token})
        except AppHTTPException:
            logger.warning("Could not revoke refresh token during sign-out.")
    clear_session_cookies(response, settings)
