from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


DateFormat = Literal["MM/dd/yyyy", "dd/MM/yyyy", "yyyy-MM-dd"]
DefaultLandingPage = Literal["FAVORITES", "PORTFOLIOS", "PROGRAMS", "SPRINTS"]
WeekdayName = Literal["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


class AccountSettingsUpdate(BaseModel):
    date_format: DateFormat | None = None
    default_landing_page: DefaultLandingPage | None = None
    hide_delivery_section: bool | None = None
    non_working_weekdays: list[WeekdayName] | None = None

    @field_validator("non_working_weekdays")
    @classmethod
    def validate_unique_weekdays(cls, weekdays: list[WeekdayName] | None) -> list[WeekdayName] | None:
        if weekdays is None:
            return weekdays
        if len(set(weekdays)) != len(weekdays):
            raise ValueError("non_working_weekdays cannot contain duplicates")
        return weekdays


class AccountSettingsRead(BaseModel):
    id: UUID
    account_id: UUID
    date_format: str
    default_landing_page: str
    hide_delivery_section: bool
    non_working_weekdays: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AccountHolidayCreate(BaseModel):
    holiday_date: date
    name: str = Field(min_length=1, max_length=255)


class AccountHolidayUpdate(BaseModel):
    holiday_date: date | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class AccountHolidayRead(BaseModel):
    id: UUID
    account_id: UUID
    holiday_date: date
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)