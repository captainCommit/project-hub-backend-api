from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.user import User
from app.routers.me import serialize_user
from app.schemas.user import UserRead
from app.services.auth import REFRESH_COOKIE, require_csrf_header
from app.services.sessions import end_session, refresh_session, start_session


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class SessionCreate(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    code_verifier: str = Field(min_length=43, max_length=128)
    redirect_uri: str = Field(min_length=1, max_length=2048)


@router.post("/session", response_model=UserRead, summary="Exchange a hosted-login code for a session cookie")
def create_session(
    session_in: SessionCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    require_csrf_header(request)
    user: User = start_session(
        db=db,
        settings=settings,
        response=response,
        code=session_in.code,
        code_verifier=session_in.code_verifier,
        redirect_uri=session_in.redirect_uri,
    )
    return serialize_user(user, settings)


@router.post("/refresh", status_code=status.HTTP_204_NO_CONTENT, summary="Renew the session cookie")
def refresh(request: Request, response: Response, settings: Settings = Depends(get_settings)) -> Response:
    require_csrf_header(request)
    refresh_session(settings=settings, response=response, refresh_token=request.cookies.get(REFRESH_COOKIE))
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End the session and revoke its refresh token")
def logout(request: Request, response: Response, settings: Settings = Depends(get_settings)) -> Response:
    require_csrf_header(request)
    end_session(settings=settings, response=response, refresh_token=request.cookies.get(REFRESH_COOKIE))
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
