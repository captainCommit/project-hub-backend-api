from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.account import PlatformAccountRead
from app.services.accounts import AccountService
from app.services.auth import get_current_user


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/accounts", response_model=list[PlatformAccountRead], summary="List every account (platform admins)")
def list_all_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PlatformAccountRead]:
    return AccountService(db).list_all_accounts(current_user=current_user)
