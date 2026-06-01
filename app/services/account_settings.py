from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account_holiday import AccountHoliday
from app.models.account_member import AccountMemberRole
from app.models.account_settings import AccountSettings
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.account_settings import AccountHolidayRepository, AccountSettingsRepository
from app.repositories.accounts import AccountRepository
from app.schemas.account_settings import AccountHolidayCreate, AccountHolidayUpdate, AccountSettingsUpdate


ACCOUNT_SETTINGS_WRITE_ROLES = {AccountMemberRole.OWNER.value, AccountMemberRole.ADMIN.value}


class AccountSettingsService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.settings = AccountSettingsRepository(db)
        self.holidays = AccountHolidayRepository(db)

    def get_settings(self, *, account_id: UUID, current_user: User) -> AccountSettings:
        self.require_account_member(account_id=account_id, user_id=current_user.id)
        settings, created = self.get_or_create_settings(account_id=account_id)
        if created:
            self.db.commit()
            self.db.refresh(settings)
        return settings

    def update_settings(
        self,
        *,
        account_id: UUID,
        settings_in: AccountSettingsUpdate,
        current_user: User,
    ) -> AccountSettings:
        self.require_account_role(
            account_id=account_id,
            user_id=current_user.id,
            allowed_roles=ACCOUNT_SETTINGS_WRITE_ROLES,
        )
        settings, _created = self.get_or_create_settings(account_id=account_id)
        changes = settings_in.model_dump(exclude_unset=True)
        if not changes:
            return settings
        try:
            settings = self.settings.update(settings, changes)
            self.db.commit()
            self.db.refresh(settings)
            return settings
        except Exception:
            self.db.rollback()
            raise

    def list_holidays(self, *, account_id: UUID, current_user: User) -> list[AccountHoliday]:
        self.require_account_member(account_id=account_id, user_id=current_user.id)
        return self.holidays.list_for_account(account_id)

    def create_holiday(
        self,
        *,
        account_id: UUID,
        holiday_in: AccountHolidayCreate,
        current_user: User,
    ) -> AccountHoliday:
        self.require_account_role(
            account_id=account_id,
            user_id=current_user.id,
            allowed_roles=ACCOUNT_SETTINGS_WRITE_ROLES,
        )
        try:
            holiday = self.holidays.create(
                account_id=account_id,
                holiday_date=holiday_in.holiday_date,
                name=holiday_in.name,
            )
            self.db.commit()
            self.db.refresh(holiday)
            return holiday
        except Exception:
            self.db.rollback()
            raise

    def update_holiday(
        self,
        *,
        holiday_id: UUID,
        holiday_in: AccountHolidayUpdate,
        current_user: User,
    ) -> AccountHoliday:
        holiday = self.get_holiday_or_404(holiday_id)
        self.require_account_role(
            account_id=holiday.account_id,
            user_id=current_user.id,
            allowed_roles=ACCOUNT_SETTINGS_WRITE_ROLES,
        )
        changes = holiday_in.model_dump(exclude_unset=True)
        if not changes:
            return holiday
        try:
            holiday = self.holidays.update(holiday, changes)
            self.db.commit()
            self.db.refresh(holiday)
            return holiday
        except Exception:
            self.db.rollback()
            raise

    def delete_holiday(self, *, holiday_id: UUID, current_user: User) -> None:
        holiday = self.get_holiday_or_404(holiday_id)
        self.require_account_role(
            account_id=holiday.account_id,
            user_id=current_user.id,
            allowed_roles=ACCOUNT_SETTINGS_WRITE_ROLES,
        )
        if not holiday.is_active:
            return
        try:
            self.holidays.deactivate(holiday)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def get_or_create_settings(self, *, account_id: UUID) -> tuple[AccountSettings, bool]:
        settings = self.settings.get_for_account(account_id)
        if settings is not None:
            return settings, False
        try:
            return self.settings.create_default(account_id), True
        except IntegrityError:
            self.db.rollback()
            settings = self.settings.get_for_account(account_id)
            if settings is None:
                raise
            return settings, False

    def get_holiday_or_404(self, holiday_id: UUID) -> AccountHoliday:
        holiday = self.holidays.get_by_id(holiday_id)
        if holiday is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account holiday not found.")
        return holiday

    def require_account_member(self, *, account_id: UUID, user_id: UUID) -> None:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")

    def require_account_role(self, *, account_id: UUID, user_id: UUID, allowed_roles: set[str]) -> None:
        self.require_account_member(account_id=account_id, user_id=user_id)
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None or membership.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")