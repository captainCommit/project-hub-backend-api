from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.hierarchy import ProjectRead, StatusSummary
from app.schemas.sprints import SprintSummary


class TaskBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    sprint_id: UUID | None = None
    parent_task_id: UUID | None = None
    task_type_id: UUID | None = None
    status_id: UUID | None = None
    priority_id: UUID | None = None
    start_date: date | None = None
    finish_date: date | None = None
    duration_days: Decimal | None = None
    story_points: int | None = Field(default=None, gt=0)
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
    priority_id: UUID | None = None
    start_date: date | None = None
    finish_date: date | None = None
    duration_days: Decimal | None = None
    story_points: int | None = Field(default=None, gt=0)
    percent_complete: Decimal | None = Field(default=None, ge=0, le=100)
    sort_order: Decimal | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "TaskUpdate":
        if self.start_date and self.finish_date and self.finish_date < self.start_date:
            raise ValueError("finish_date cannot be before start_date")
        return self


class TaskBulkUpdateFields(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status_id: UUID | None = None
    priority_id: UUID | None = None
    task_type_id: UUID | None = None
    start_date: date | None = None
    finish_date: date | None = None
    duration_days: Decimal | None = None
    story_points: int | None = Field(default=None, gt=0)
    percent_complete: Decimal | None = Field(default=None, ge=0, le=100)
    assigned_to: UUID | None = None
    sprint_id: UUID | None = None
    parent_task_id: UUID | None = None
    sort_order: Decimal | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "TaskBulkUpdateFields":
        if self.start_date and self.finish_date and self.finish_date < self.start_date:
            raise ValueError("finish_date cannot be before start_date")
        return self


class TaskBulkUpdateItem(BaseModel):
    id: UUID
    fields: TaskBulkUpdateFields

    @model_validator(mode="after")
    def validate_has_fields(self) -> "TaskBulkUpdateItem":
        if not self.fields.model_fields_set:
            raise ValueError("fields must include at least one value")
        return self


class TaskBulkUpdateRequest(BaseModel):
    updates: list[TaskBulkUpdateItem] = Field(min_length=1)


class TaskBulkDeleteRequest(BaseModel):
    task_ids: list[UUID] = Field(min_length=1)


class TaskReorderItem(BaseModel):
    id: UUID
    parent_task_id: UUID | None = None
    sort_order: Decimal


class TaskReorderRequest(BaseModel):
    tasks: list[TaskReorderItem] = Field(min_length=1)


class TaskMoveRequest(BaseModel):
    parent_task_id: UUID | None = None
    sort_order: Decimal


class TaskBoardPositionUpdate(BaseModel):
    status_id: UUID | None
    sort_order: Decimal
    sprint_id: UUID | None


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
    priority_id: UUID | None
    name: str
    description: str | None
    start_date: date | None
    finish_date: date | None
    duration_days: Decimal | None
    story_points: int | None
    percent_complete: Decimal
    sort_order: Decimal
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    status: StatusSummary | None = None
    task_type: StatusSummary | None = None
    priority: StatusSummary | None = None
    sprint: SprintSummary | None = None
    assignments: list[TaskAssignmentRead] = []
    predecessors: list[TaskPredecessorRead] = []

    model_config = ConfigDict(from_attributes=True)


class TaskTreeRead(TaskRead):
    children: list["TaskTreeRead"] = []


class TaskBoardColumnRead(BaseModel):
    id: UUID | None
    status_id: UUID | None
    label: str
    value: str | None
    color: str | None
    sort_order: int | None
    is_uncategorized: bool = False
    tasks: list[TaskRead] = Field(default_factory=list)


class TaskBoardRead(BaseModel):
    project_id: UUID
    sprint_id: UUID | None = None
    columns: list[TaskBoardColumnRead]


class TaskResourceSummaryRead(BaseModel):
    id: UUID | None
    user_id: UUID | None = None
    name: str
    role: str | None = None
    allocated_hours: Decimal | None = None
    source: Literal["ALLOCATION", "ASSIGNMENT"]


class TaskGanttTaskRead(BaseModel):
    id: UUID
    parent_task_id: UUID | None
    name: str
    task_type: StatusSummary | None
    start_date: date | None
    finish_date: date | None
    duration_days: Decimal | None
    percent_complete: Decimal
    sort_order: Decimal
    resources: list[TaskResourceSummaryRead] = Field(default_factory=list)
    predecessors: list[TaskPredecessorRead] = Field(default_factory=list)


class ProjectTaskGanttRead(BaseModel):
    project: ProjectRead
    tasks: list[TaskGanttTaskRead]


class TaskProjectSummaryRead(BaseModel):
    id: UUID
    name: str


class TaskProgramSummaryRead(BaseModel):
    id: UUID
    name: str


class DueTaskRead(BaseModel):
    id: UUID
    project_id: UUID
    sprint_id: UUID | None
    parent_task_id: UUID | None
    name: str
    finish_date: date
    start_date: date | None
    duration_days: Decimal | None
    percent_complete: Decimal
    sort_order: Decimal
    due_status: Literal["OVERDUE", "UPCOMING"]
    project: TaskProjectSummaryRead
    program: TaskProgramSummaryRead
    status: StatusSummary | None
    resources: list[TaskResourceSummaryRead] = Field(default_factory=list)


class DueTasksRead(BaseModel):
    mode: Literal["OVERDUE", "UPCOMING", "BOTH"]
    days: int
    tasks: list[DueTaskRead]
    overdue: list[DueTaskRead]
    upcoming: list[DueTaskRead]