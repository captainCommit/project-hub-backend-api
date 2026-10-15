from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.hierarchy import StatusSummary


HomeItemKind = Literal["TASK", "RISK", "ISSUE"]
HomeDueBucket = Literal["OVERDUE", "WEEK", "UPCOMING"]


class HomeProjectRef(BaseModel):
    id: UUID
    name: str


class HomePriorityItem(BaseModel):
    id: UUID
    kind: HomeItemKind
    title: str
    project: HomeProjectRef
    due_date: date
    bucket: HomeDueBucket
    # The status that marks this item done (Complete for tasks, Closed for risks and issues); null if the account has none.
    complete_status_id: UUID | None


class HomePriorityCounts(BaseModel):
    all: int
    overdue: int
    week: int
    upcoming: int


class HomeMilestone(BaseModel):
    id: UUID
    name: str
    finish_date: date


class HomeProject(BaseModel):
    id: UUID
    name: str
    status: StatusSummary | None
    is_completed: bool
    at_risk: bool
    percent_complete: int
    next_milestone: HomeMilestone | None
    target_end_date: date | None


class HomeActivity(BaseModel):
    id: UUID
    entity_type: str
    entity_id: UUID
    action: str
    entity_name: str | None
    actor_name: str | None
    project: HomeProjectRef | None
    created_at: datetime


class HomeRead(BaseModel):
    priorities: list[HomePriorityItem] = Field(default_factory=list)
    counts: HomePriorityCounts
    active_project_count: int
    my_projects: list[HomeProject] = Field(default_factory=list)
    recent_activity: list[HomeActivity] = Field(default_factory=list)
