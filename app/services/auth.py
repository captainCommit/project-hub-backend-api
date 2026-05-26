from typing import Any

import requests
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.user import User
from app.repositories.users import UserRepository


DEV_USER_EMAIL = "dev@example.com"
DEV_USER_FULL_NAME = "Dev User"
COGNITO_ALLOWED_TOKEN_USES = {"id", "access"}

bearer_scheme = HTTPBearer(auto_error=False)
_jwks_cache: dict[str, Any] | None = None


def auth_error(detail: str = "Not authenticated.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_or_create_dev_user(db: Session) -> User:
    user_repository = UserRepository(db)
    user = user_repository.get_by_email(DEV_USER_EMAIL)
    if user is None:
        user = user_repository.create(email=DEV_USER_EMAIL, full_name=DEV_USER_FULL_NAME)
        db.commit()
        db.refresh(user)
    return user


def get_cognito_jwks(settings: Settings) -> dict[str, Any]:
    global _jwks_cache
    if _jwks_cache is None:
        response = requests.get(settings.cognito_jwks_url, timeout=5)
        response.raise_for_status()
        _jwks_cache = response.json()
    return _jwks_cache


def get_jwk_for_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise auth_error("Invalid token.") from exc

    kid = header.get("kid")
    if not kid:
        raise auth_error("Invalid token.")

    jwks = get_cognito_jwks(settings)
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key

    raise auth_error("Invalid token.")


def validate_cognito_token(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.cognito_user_pool_id or not settings.cognito_app_client_id:
        raise auth_error("Cognito authentication is not configured.")

    key = get_jwk_for_token(token, settings)
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.cognito_issuer,
            options={"verify_aud": False},
        )
    except ExpiredSignatureError as exc:
        raise auth_error("Token has expired.") from exc
    except JWTError as exc:
        raise auth_error("Invalid token.") from exc

    token_use = claims.get("token_use")
    if token_use not in COGNITO_ALLOWED_TOKEN_USES:
        raise auth_error("Invalid token.")

    if token_use == "id" and claims.get("aud") != settings.cognito_app_client_id:
        raise auth_error("Invalid token audience.")

    if token_use == "access" and claims.get("client_id") != settings.cognito_app_client_id:
        raise auth_error("Invalid token client.")

    if not claims.get("sub") or not claims.get("email"):
        raise auth_error("Token is missing required claims.")

    return claims


def sync_cognito_user(db: Session, claims: dict[str, Any]) -> User:
    cognito_sub = str(claims["sub"])
    email = str(claims["email"])
    full_name = claims.get("name")

    user_repository = UserRepository(db)
    user = user_repository.get_by_cognito_sub(cognito_sub)
    if user is None:
        user = user_repository.create(
            cognito_sub=cognito_sub,
            email=email,
            full_name=full_name,
        )
        db.commit()
        db.refresh(user)
        return user

    if user.email != email or user.full_name != full_name:
        user = user_repository.update_identity(user, email=email, full_name=full_name)
        db.commit()
        db.refresh(user)

    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if settings.auth_mode == "local":
        if settings.environment != "local":
            raise auth_error("Local auth mode is only allowed when ENVIRONMENT=local.")
        return get_or_create_dev_user(db)

    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise auth_error("Missing bearer token.")

    claims = validate_cognito_token(credentials.credentials, settings)
    return sync_cognito_user(db, claims)