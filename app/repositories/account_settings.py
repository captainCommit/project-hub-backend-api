from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account_holiday import AccountHoliday
from app.models.account_settings import (
    AccountSettings,
    DEFAULT_DATE_FORMAT,
    DEFAULT_LANDING_PAGE,
    default_non_working_weekdays,
)


class AccountSettingsRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_for_account(self, account_id: UUID) -> AccountSettings | None:
        statement = select(AccountSettings).where(AccountSettings.account_id == account_id)
        return self.db.scalar(statement)

    def create_default(self, account_id: UUID) -> AccountSettings:
        settings = AccountSettings(
            account_id=account_id,
            date_format=DEFAULT_DATE_FORMAT,
            default_landing_page=DEFAULT_LANDING_PAGE,
            hide_delivery_section=False,
            non_working_weekdays=default_non_working_weekdays(),
        )
        self.db.add(settings)
        self.db.flush()
        self.db.refresh(settings)
        return settings

    def update(self, settings: AccountSettings, changes: dict[str, object]) -> AccountSettings:
        for field, value in changes.items():
            setattr(settings, field, value)
        self.db.add(settings)
        self.db.flush()
        self.db.refresh(settings)
        return settings


class AccountHolidayRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, holiday_id: UUID) -> AccountHoliday | None:
        return self.db.get(AccountHoliday, holiday_id)

    def list_for_account(self, account_id: UUID) -> list[AccountHoliday]:
        statement = (
            select(AccountHoliday)
            .where(AccountHoliday.account_id == account_id, AccountHoliday.is_active.is_(True))
            .order_by(AccountHoliday.holiday_date, AccountHoliday.name, AccountHoliday.id)
        )
        return list(self.db.scalars(statement).all())

    def list_active_dates_for_account(self, *, account_id: UUID, start_date: date, end_date: date) -> set[date]:
        statement = select(AccountHoliday.holiday_date).where(
            AccountHoliday.account_id == account_id,
            AccountHoliday.is_active.is_(True),
            AccountHoliday.holiday_date >= start_date,
            AccountHoliday.holiday_date <= end_date,
        )
        return set(self.db.scalars(statement).all())

    def create(self, **values: object) -> AccountHoliday:
        holiday = AccountHoliday(**values)
        self.db.add(holiday)
        self.db.flush()
        self.db.refresh(holiday)
        return holiday

    def update(self, holiday: AccountHoliday, changes: dict[str, object]) -> AccountHoliday:
        for field, value in changes.items():
            setattr(holiday, field, value)
        self.db.add(holiday)
        self.db.flush()
        self.db.refresh(holiday)
        return holiday

    def deactivate(self, holiday: AccountHoliday) -> AccountHoliday:
        holiday.is_active = False
        self.db.add(holiday)
        self.db.flush()
        self.db.refresh(holiday)
        return holiday