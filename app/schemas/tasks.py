from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.hierarchy import StatusSummary
from app.schemas.sprints import SprintSummary


class TaskBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    sprint_id: UUID | None = None
    parent_task_id: UUID | None = None
    task_type_id: UUID | None = None
    status_id: UUID | None = None
    start_date: date | None = None
    finish_date: date | None = None
    duration_days: Decimal | None = None
    percent_complete: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    sort_order: Decimal = Decimal("0")

    @model_validator(mode="after")
    def validate_dates(self) -> "TaskBase":
        if self.start_date and self.finish_date and self.finish_date < self.start_date:
            raise ValueError("finish_date cannot be before start_date")
        return self


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    sprint_id: UUID | None = None
    parent_task_id: UUID | None = None
    task_type_id: UUID | None = None
    status_id: UUID | None = None
    start_date: date | None = None
    finish_date: date | None = None
    duration_days: Decimal | None = None
    percent_complete: Decimal | None = Field(default=None, ge=0, le=100)
    sort_order: Decimal | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "TaskUpdate":
        if self.start_date and self.finish_date and self.finish_date < self.start_date:
            raise ValueError("finish_date cannot be before start_date")
        return self


class TaskAssignmentCreate(BaseModel):
    user_id: UUID | None = None
    resource_name: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_assignee(self) -> "TaskAssignmentCreate":
        if self.user_id is None and not self.resource_name:
            raise ValueError("user_id or resource_name is required")
        return self


class TaskAssignmentRead(BaseModel):
    id: UUID
    account_id: UUID
    task_id: UUID
    user_id: UUID | None
    resource_name: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskPredecessorCreate(BaseModel):
    predecessor_task_id: UUID
    dependency_type: Literal["FS", "SS", "FF", "SF"] = "FS"
    lag_days: int = 0


class TaskPredecessorRead(BaseModel):
    id: UUID
    account_id: UUID
    task_id: UUID
    predecessor_task_id: UUID
    dependency_type: Literal["FS", "SS", "FF", "SF"]
    lag_days: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskRead(BaseModel):
    id: UUID
    account_id: UUID
    project_id: UUID
    sprint_id: UUID | None
    parent_task_id: UUID | None
    task_type_id: UUID | None
    status_id: UUID | None
    name: str
    description: str | None
    start_date: date | None
    finish_date: date | None
    duration_days: Decimal | None
    percent_complete: Decimal
    sort_order: Decimal
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    status: StatusSummary | None = None
    task_type: StatusSummary | None = None
    sprint: SprintSummary | None = None
    assignments: list[TaskAssignmentRead] = []
    predecessors: list[TaskPredecessorRead] = []

    model_config = ConfigDict(from_attributes=True)


class TaskTreeRead(TaskRead):
    children: list["TaskTreeRead"] = []