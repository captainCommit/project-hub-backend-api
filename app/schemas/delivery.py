from datetime import date
from uuid import UUID

from pydantic import BaseModel

from app.schemas.hierarchy import StatusSummary


class DeliverySprintSummary(BaseModel):
    id: UUID
    name: str
    status_id: UUID | None
    status: StatusSummary | None = None
    start_date: date | None
    end_date: date | None
    total_stories: int
    done_stories: int
    total_story_points: int
    completed_story_points: int


class DeliveryProjectSummary(BaseModel):
    id: UUID
    name: str
    total_sprints: int
    active_sprints: int
    total_stories: int
    done_stories: int
    sprints: list[DeliverySprintSummary]


class DeliveryProgramSummary(BaseModel):
    id: UUID
    name: str
    total_sprints: int
    active_sprints: int
    total_stories: int
    done_stories: int
    projects: list[DeliveryProjectSummary]


class DeliveryOverviewRead(BaseModel):
    total_sprints: int
    active_sprints: int
    total_stories: int
    done_stories: int
    programs: list[DeliveryProgramSummary]