from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.hierarchy import StatusSummary


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


class ResourceTimeOffCreate(BaseModel):
    start_date: date
    end_date: date
    reason: str | None = Field(default=None, max_length=255)
    hours_per_day: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_dates(self) -> "ResourceTimeOffCreate":
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class ResourceTimeOffUpdate(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    reason: str | None = Field(default=None, max_length=255)
    hours_per_day: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_dates(self) -> "ResourceTimeOffUpdate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class ResourceTimeOffRead(BaseModel):
    id: UUID
    account_id: UUID
    resource_id: UUID
    start_date: date
    end_date: date
    reason: str | None
    hours_per_day: Decimal | None
    created_by: UUID | None
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
    base_capacity_hours: Decimal
    time_off_hours: Decimal
    available_hours: Decimal
    utilization_percent: float
    overallocated: bool


class ResourceCalendarRead(BaseModel):
    start_date: date
    end_date: date
    resources: list[ResourceCalendarResourceRead]


class ResourceCapacityForecastWeekRead(BaseModel):
    week_start: date
    week_end: date
    base_capacity_hours: Decimal
    time_off_hours: Decimal
    available_hours: Decimal
    allocated_hours: Decimal
    remaining_hours: Decimal
    utilization_percent: float
    overallocated: bool


class ResourceCapacityForecastResourceRead(BaseModel):
    resource: ResourceCalendarResourceSummary
    weeks: list[ResourceCapacityForecastWeekRead]


class ResourceCapacityForecastRead(ResourceCapacityForecastResourceRead):
    start_date: date
    end_date: date


class AccountCapacityForecastSummaryRead(BaseModel):
    resource_count: int
    overallocated_resource_count: int
    total_base_capacity_hours: Decimal
    total_time_off_hours: Decimal
    total_available_hours: Decimal
    total_allocated_hours: Decimal
    total_remaining_hours: Decimal
    average_utilization_percent: float


class AccountCapacityForecastRead(BaseModel):
    start_date: date
    end_date: date
    resources: list[ResourceCapacityForecastResourceRead]
    summary: AccountCapacityForecastSummaryRead


class ResourceAnalysisSummaryRead(BaseModel):
    resource_count: int
    overallocated_count: int
    underutilized_count: int
    unassigned_task_count: int
    skill_gap_count: int


class ResourceAnalysisResourceRead(BaseModel):
    resource: ResourceCalendarResourceSummary
    allocated_hours: Decimal
    available_hours: Decimal
    remaining_hours: Decimal
    utilization_percent: float
    overallocated: bool = False


class ResourceAnalysisTaskSummary(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    start_date: date | None
    finish_date: date | None
    priority: StatusSummary | None = None
    project: ResourceCalendarProjectSummary
    program: ResourceCalendarProgramSummary


class ResourceAnalysisSkillSummary(BaseModel):
    id: UUID
    name: str
    category: str | None
    required_proficiency: str | None = None


class ResourceAnalysisUnassignedTaskRead(BaseModel):
    task: ResourceAnalysisTaskSummary
    reasons: list[str] = Field(default_factory=list)


class ResourceAnalysisSkillGapRead(BaseModel):
    task: ResourceAnalysisTaskSummary
    skill: ResourceAnalysisSkillSummary
    missing_skills: list[str] = Field(default_factory=list)
    assigned_resources: list[ResourceCalendarResourceSummary] = Field(default_factory=list)
    message: str


class ResourceAnalysisFutureShortageRead(BaseModel):
    skill: str
    period_start: date
    period_end: date
    required_hours: Decimal
    available_hours: Decimal
    shortage_hours: Decimal


class ResourceAnalysisSuggestionRead(BaseModel):
    type: str
    message: str
    resource: ResourceCalendarResourceSummary | None = None
    task: ResourceAnalysisTaskSummary | None = None
    skill: ResourceAnalysisSkillSummary | None = None


class ResourceAnalysisRead(BaseModel):
    summary: ResourceAnalysisSummaryRead
    overallocated_resources: list[ResourceAnalysisResourceRead]
    underutilized_resources: list[ResourceAnalysisResourceRead]
    unassigned_tasks: list[ResourceAnalysisUnassignedTaskRead]
    critical_unstaffed_tasks: list[ResourceAnalysisUnassignedTaskRead] = Field(default_factory=list)
    skill_gaps: list[ResourceAnalysisSkillGapRead]
    future_shortages: list[ResourceAnalysisFutureShortageRead] = Field(default_factory=list)
    suggestions: list[ResourceAnalysisSuggestionRead]


class ResourceRecommendationRead(BaseModel):
    resource: ResourceCalendarResourceSummary
    score: int = Field(ge=0, le=100)
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)