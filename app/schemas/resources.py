from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResourceCreate(BaseModel):
    user_id: UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    weekly_capacity_hours: Decimal = Field(default=Decimal("40"), gt=0)


class ResourceUpdate(BaseModel):
    user_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    weekly_capacity_hours: Decimal | None = Field(default=None, gt=0)
    is_active: bool | None = None


class ResourceRead(BaseModel):
    id: UUID
    account_id: UUID
    user_id: UUID | None
    name: str
    role: str | None
    weekly_capacity_hours: Decimal
    is_active: bool
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResourceAllocationCreate(BaseModel):
    resource_id: UUID
    allocated_hours: Decimal | None = Field(default=None, gt=0)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "ResourceAllocationCreate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class ResourceAllocationUpdate(BaseModel):
    resource_id: UUID | None = None
    allocated_hours: Decimal | None = Field(default=None, gt=0)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "ResourceAllocationUpdate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class ResourceAllocationRead(BaseModel):
    id: UUID
    account_id: UUID
    task_id: UUID
    resource_id: UUID
    allocated_hours: Decimal | None
    start_date: date | None
    end_date: date | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResourceCalendarResourceSummary(BaseModel):
    id: UUID
    name: str
    role: str | None
    weekly_capacity_hours: Decimal


class ResourceCalendarTaskSummary(BaseModel):
    id: UUID
    name: str
    start_date: date | None
    finish_date: date | None


class ResourceCalendarProjectSummary(BaseModel):
    id: UUID
    name: str


class ResourceCalendarProgramSummary(BaseModel):
    id: UUID
    name: str


class ResourceCalendarAllocationRead(BaseModel):
    id: UUID
    task: ResourceCalendarTaskSummary
    project: ResourceCalendarProjectSummary
    program: ResourceCalendarProgramSummary
    allocated_hours: Decimal | None
    start_date: date | None
    end_date: date | None


class ResourceCalendarResourceRead(BaseModel):
    resource: ResourceCalendarResourceSummary
    allocations: list[ResourceCalendarAllocationRead]
    total_allocated_hours: Decimal
    weekly_capacity_hours: Decimal
    utilization_percent: float
    overallocated: bool


class ResourceCalendarRead(BaseModel):
    start_date: date
    end_date: date
    resources: list[ResourceCalendarResourceRead]