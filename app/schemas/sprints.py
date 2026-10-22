from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.hierarchy import StatusSummary


class SprintBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    goal: str | None = None
    status_id: UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    # Story points the team can take on in the sprint.
    capacity: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_dates(self) -> "SprintBase":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class SprintCreate(SprintBase):
    pass


class SprintUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    goal: str | None = None
    status_id: UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    capacity: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_dates(self) -> "SprintUpdate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class SprintRead(BaseModel):
    id: UUID
    account_id: UUID
    project_id: UUID
    name: str
    goal: str | None
    status_id: UUID | None
    start_date: date | None
    end_date: date | None
    capacity: int | None = None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    status: StatusSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class SprintSummary(BaseModel):
    id: UUID
    name: str
    status: StatusSummary | None = None
    start_date: date | None
    end_date: date | None

    model_config = ConfigDict(from_attributes=True)


class StoryPointLegendItem(BaseModel):
    points: int
    label: str


class SprintMetricsRead(BaseModel):
    sprint: SprintRead
    total_tasks: int
    total_story_points: int
    completed_story_points: int
    remaining_story_points: int
    completion_percent: int
    days_remaining: int
    story_point_legend: list[StoryPointLegendItem]