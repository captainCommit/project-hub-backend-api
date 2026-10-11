from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.account import PlatformAccountRead
from app.schemas.platform import BillingSummary, SystemStatus
from app.services.accounts import AccountService
from app.services.auth import get_current_user
from app.services.platform import PlatformService


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/accounts", response_model=list[PlatformAccountRead], summary="List every account (platform admins)")
def list_all_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PlatformAccountRead]:
    return AccountService(db).list_all_accounts(current_user=current_user)


@router.get("/billing", response_model=BillingSummary, summary="Seats and monthly cost per account (platform admins)")
def get_billing_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> BillingSummary:
    return PlatformService(db, settings=settings).billing_summary(current_user=current_user)


@router.get("/system", response_model=SystemStatus, summary="API, database and usage status (platform admins)")
def get_system_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> SystemStatus:
    return PlatformService(db, settings=settings).system_status(current_user=current_user)
