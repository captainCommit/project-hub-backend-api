from typing import Any

import requests
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.errors import AppHTTPException
from app.models.user import User
from app.repositories.users import UserRepository


DEV_USER_EMAIL = "dev@example.com"
DEV_USER_FULL_NAME = "Dev User"
COGNITO_ALLOWED_TOKEN_USES = {"id", "access"}

# Browser sessions: the ID token travels in an HttpOnly cookie scoped to the API.
SESSION_COOKIE = "ph_session"
REFRESH_COOKIE = "ph_refresh"
# Cookie-authenticated writes must carry this header. Cross-site pages can't set custom
# headers without a CORS preflight, so this backs up SameSite=Strict against CSRF.
CSRF_HEADER = "X-Requested-With"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

bearer_scheme = HTTPBearer(auto_error=False)
_jwks_cache: dict[str, dict[str, Any]] = {}


class AuthErrorCode:
    AUTH_REQUIRED = "AUTH_REQUIRED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_INVALID = "TOKEN_INVALID"
    AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
    AUTH_UNAVAILABLE = "AUTH_UNAVAILABLE"
    USER_DISABLED = "USER_DISABLED"
    CSRF_CHECK_FAILED = "CSRF_CHECK_FAILED"
    ACCOUNT_CONFLICT = "ACCOUNT_CONFLICT"


def auth_error(detail: str = "Not authenticated.", error_code: str | None = None) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"}
    if error_code is None:
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers=headers)
    return AppHTTPException(status.HTTP_401_UNAUTHORIZED, error_code, detail, headers=headers)


def get_or_create_dev_user(db: Session) -> User:
    user_repository = UserRepository(db)
    user = user_repository.get_by_email(DEV_USER_EMAIL)
    if user is None:
        user = user_repository.create(email=DEV_USER_EMAIL, full_name=DEV_USER_FULL_NAME)
        # Local dev only: the single dev user can do everything, including creating accounts.
        user.is_platform_admin = True
        db.commit()
        db.refresh(user)
    return user


def get_cognito_jwks(settings: Settings) -> dict[str, Any]:
    url = settings.cognito_jwks_url
    if url not in _jwks_cache:
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise AppHTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                AuthErrorCode.AUTH_UNAVAILABLE,
                "The sign-in service is unavailable. Please try again shortly.",
            ) from exc
        _jwks_cache[url] = response.json()
    return _jwks_cache[url]


def get_jwk_for_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise auth_error("Invalid token.", AuthErrorCode.TOKEN_INVALID) from exc

    kid = header.get("kid")
    if not kid:
        raise auth_error("Invalid token.", AuthErrorCode.TOKEN_INVALID)

    for key in get_cognito_jwks(settings).get("keys", []):
        if key.get("kid") == kid:
            return key

    raise auth_error("Invalid token.", AuthErrorCode.TOKEN_INVALID)


def validate_cognito_token(token: str, settings: Settings, access_token: str | None = None) -> dict[str, Any]:
    """Validate a Cognito ID or access token.

    Hosted-login ID tokens carry at_hash. It is checked when the paired access token is
    at hand (code exchange, refresh); a stored ID token on its own is validated without it.
    """
    if not settings.cognito_user_pool_id or not settings.cognito_app_client_id:
        raise AppHTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            AuthErrorCode.AUTH_NOT_CONFIGURED,
            "Cognito authentication is not configured.",
        )

    key = get_jwk_for_token(token, settings)
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.cognito_issuer,
            access_token=access_token,
            options={"verify_aud": False, "verify_at_hash": access_token is not None},
        )
    except ExpiredSignatureError as exc:
        raise auth_error("Your session has expired.", AuthErrorCode.TOKEN_EXPIRED) from exc
    except JWTError as exc:
        raise auth_error("Invalid token.", AuthErrorCode.TOKEN_INVALID) from exc

    token_use = claims.get("token_use")
    if token_use not in COGNITO_ALLOWED_TOKEN_USES:
        raise auth_error("Invalid token.", AuthErrorCode.TOKEN_INVALID)

    if token_use == "id" and claims.get("aud") not in settings.cognito_allowed_client_ids:
        raise auth_error("Invalid token audience.", AuthErrorCode.TOKEN_INVALID)

    if token_use == "access" and claims.get("client_id") not in settings.cognito_allowed_client_ids:
        raise auth_error("Invalid token client.", AuthErrorCode.TOKEN_INVALID)

    if not claims.get("sub") or not claims.get("email"):
        raise auth_error("Token is missing required claims.", AuthErrorCode.TOKEN_INVALID)

    return claims


def email_is_verified(claims: dict[str, Any]) -> bool:
    return claims.get("email_verified") in (True, "true", "True")


def sync_cognito_user(db: Session, claims: dict[str, Any], settings: Settings | None = None) -> User:
    cognito_sub = str(claims["sub"])
    email = str(claims["email"])
    full_name = claims.get("name")

    user_repository = UserRepository(db)
    user = user_repository.get_by_cognito_sub(cognito_sub)
    if user is None:
        existing = user_repository.get_by_email_normalized(email)
        if existing is not None:
            # Users invited through the app (no sub yet) or moved to a new user pool (old sub)
            # are linked by email - but only an email Cognito has verified can claim an account.
            if existing.cognito_sub is not None and not email_is_verified(claims):
                raise AppHTTPException(
                    status.HTTP_409_CONFLICT,
                    AuthErrorCode.ACCOUNT_CONFLICT,
                    "This email is already linked to another sign-in. Verify your email or contact an administrator.",
                )
            first_sign_in = existing.cognito_sub is None
            existing.cognito_sub = cognito_sub
            user = existing
        else:
            first_sign_in = False
            user = user_repository.create(cognito_sub=cognito_sub, email=email, full_name=full_name)
        db.commit()
        db.refresh(user)
        if first_sign_in and settings is not None:
            # An invite just turned into a seat in every account they belong to.
            from app.services.billing import BillingService

            BillingService(db, settings=settings).sync_seats_for_user(user.id)
        return user

    # The name in the user's profile wins: Cognito's name only fills it in when the profile has none.
    next_full_name = user.full_name or full_name
    if user.email != email or user.full_name != next_full_name:
        user = user_repository.update_identity(user, email=email, full_name=next_full_name)
        db.commit()
        db.refresh(user)

    return user


def token_from_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> tuple[str | None, bool]:
    """Bearer header (API clients) or session cookie (browser). Returns (token, from_cookie)."""
    if credentials is not None and credentials.scheme.lower() == "bearer" and credentials.credentials:
        return credentials.credentials, False
    cookie_token = request.cookies.get(SESSION_COOKIE)
    if cookie_token:
        return cookie_token, True
    return None, False


def require_csrf_header(request: Request) -> None:
    if request.method not in SAFE_METHODS and not request.headers.get(CSRF_HEADER):
        raise AppHTTPException(
            status.HTTP_403_FORBIDDEN,
            AuthErrorCode.CSRF_CHECK_FAILED,
            f"Missing {CSRF_HEADER} header.",
        )


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if settings.auth_mode == "local":
        if settings.environment != "local":
            raise auth_error("Local auth mode is only allowed when ENVIRONMENT=local.")
        return get_or_create_dev_user(db)

    token, from_cookie = token_from_request(request, credentials)
    if token is None:
        raise auth_error("Sign in to continue.", AuthErrorCode.AUTH_REQUIRED)
    if from_cookie:
        require_csrf_header(request)

    claims = validate_cognito_token(token, settings)
    user = sync_cognito_user(db, claims, settings)
    if not user.is_active:
        raise AppHTTPException(
            status.HTTP_403_FORBIDDEN,
            AuthErrorCode.USER_DISABLED,
            "Your Project Hub user is disabled. Contact an administrator.",
        )
    return user
