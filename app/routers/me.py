from fastapi import APIRouter, Depends

from app.models.user import User
from app.schemas.user import UserRead
from app.services.auth import get_current_user


router = APIRouter(prefix="/api/v1", tags=["me"])


@router.get("/me", response_model=UserRead)
def read_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user