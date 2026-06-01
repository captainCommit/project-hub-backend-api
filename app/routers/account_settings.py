from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.account_settings import (
    AccountHolidayCreate,
    AccountHolidayRead,
    AccountHolidayUpdate,
    AccountSettingsRead,
    AccountSettingsUpdate,
)
from app.services.account_settings import AccountSettingsService
from app.services.auth import get_current_user


router = APIRouter(prefix="/api/v1", tags=["account-settings"])


@router.get("/accounts/{account_id}/settings", response_model=AccountSettingsRead)
def get_account_settings(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountSettingsRead:
    return AccountSettingsService(db).get_settings(account_id=account_id, current_user=current_user)


@router.patch("/accounts/{account_id}/settings", response_model=AccountSettingsRead)
def update_account_settings(
    account_id: UUID,
    settings_in: AccountSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountSettingsRead:
    return AccountSettingsService(db).update_settings(
        account_id=account_id,
        settings_in=settings_in,
        current_user=current_user,
    )


@router.get("/accounts/{account_id}/holidays", response_model=list[AccountHolidayRead])
def list_account_holidays(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AccountHolidayRead]:
    return AccountSettingsService(db).list_holidays(account_id=account_id, current_user=current_user)


@router.post("/accounts/{account_id}/holidays", response_model=AccountHolidayRead, status_code=status.HTTP_201_CREATED)
def create_account_holiday(
    account_id: UUID,
    holiday_in: AccountHolidayCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountHolidayRead:
    return AccountSettingsService(db).create_holiday(
        account_id=account_id,
        holiday_in=holiday_in,
        current_user=current_user,
    )


@router.patch("/account-holidays/{holiday_id}", response_model=AccountHolidayRead)
def update_account_holiday(
    holiday_id: UUID,
    holiday_in: AccountHolidayUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountHolidayRead:
    return AccountSettingsService(db).update_holiday(
        holiday_id=holiday_id,
        holiday_in=holiday_in,
        current_user=current_user,
    )


@router.delete("/account-holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account_holiday(
    holiday_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    AccountSettingsService(db).delete_holiday(holiday_id=holiday_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)