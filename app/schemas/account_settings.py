from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


DateFormat = Literal["MM/dd/yyyy", "dd/MM/yyyy", "yyyy-MM-dd", "D MMM YYYY"]
DefaultLandingPage = Literal["FAVORITES", "PORTFOLIOS", "PROGRAMS", "SPRINTS"]
WeekdayName = Literal["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]

# The statuses Settings > Status & Priority Labels can rename that are not stored as option values.
STATUS_LABEL_KEYS = {
    "WORK_STATUS": {"not_started", "in_progress", "on_hold", "completed", "cancelled"},
    "HEALTH": {"green", "amber", "red", "grey", "na"},
    # Test case results (test_cases.result NOT_EXECUTED / PASS / FAIL).
    "TEST_RESULT": {"not_executed", "pass", "fail"},
}


class StatusLabel(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


def validate_status_labels(labels: dict[str, dict[str, StatusLabel]] | None) -> dict[str, dict[str, StatusLabel]] | None:
    for group, entries in (labels or {}).items():
        allowed = STATUS_LABEL_KEYS.get(group)
        if allowed is None:
            raise ValueError(f"Unknown status label group: {group}")
        unknown = set(entries) - allowed
        if unknown:
            raise ValueError(f"Unknown {group} statuses: {', '.join(sorted(unknown))}")
    return labels


class AccountSettingsUpdate(BaseModel):
    date_format: DateFormat | None = None
    default_landing_page: DefaultLandingPage | None = None
    hide_delivery_section: bool | None = None
    hide_uncategorized_column: bool | None = None
    non_working_weekdays: list[WeekdayName] | None = None
    # Replaces all renamed labels; {} (or null) goes back to the defaults.
    status_labels: dict[str, dict[str, StatusLabel]] | None = None

    _check_status_labels = field_validator("status_labels")(validate_status_labels)

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
    hide_uncategorized_column: bool = False
    non_working_weekdays: list[str]
    status_labels: dict[str, dict[str, StatusLabel]] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("status_labels", mode="before")
    @classmethod
    def none_as_empty(cls, value: object) -> object:
        return value or {}


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