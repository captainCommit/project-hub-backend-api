from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.hierarchy import StatusSummary


HealthStatus = Literal["GREEN", "YELLOW", "RED", "UNKNOWN"]


class ProjectOverviewProjectSummary(BaseModel):
    id: UUID
    account_id: UUID
    portfolio_id: UUID
    program_id: UUID
    name: str
    description: str | None
    status: StatusSummary | None
    start_date: date | None
    target_end_date: date | None


class ProjectOverviewStats(BaseModel):
    total_tasks: int
    completed_tasks: int
    in_progress_tasks: int
    overdue_tasks: int
    upcoming_milestones: int
    open_risks: int
    open_issues: int
    pending_decisions: int
    open_assumptions: int
    resource_count: int
    overallocated_resources: int


class ProjectOverviewHealth(BaseModel):
    schedule: HealthStatus
    scope: HealthStatus
    resources: HealthStatus
    overall: HealthStatus


class ProjectOverviewTaskSummary(BaseModel):
    id: UUID
    name: str
    status: StatusSummary | None
    task_type: StatusSummary | None
    start_date: date | None
    finish_date: date | None


class ProjectOverviewRiskSummary(BaseModel):
    id: UUID
    risk_number: str
    title: str
    priority: StatusSummary | None
    status: StatusSummary | None
    target_resolution_date: date | None
    created_at: datetime


class ProjectOverviewIssueSummary(BaseModel):
    id: UUID
    issue_number: str
    title: str
    priority: StatusSummary | None
    status: StatusSummary | None
    target_resolution_date: date | None
    created_at: datetime


class ProjectOverviewDecisionSummary(BaseModel):
    id: UUID
    decision_number: str
    title: str
    status: StatusSummary | None
    proposed_date: date | None
    approved_date: date | None
    created_at: datetime


class ProjectOverviewActivitySummary(BaseModel):
    id: UUID
    entity_type: str
    entity_id: UUID
    action: str
    created_by: UUID | None
    created_at: datetime


class ProjectOverviewResourceSummary(BaseModel):
    total_resources: int
    total_allocated_hours: float
    overallocated_resources: int


class ProjectOverviewRead(BaseModel):
    project: ProjectOverviewProjectSummary
    stats: ProjectOverviewStats
    health: ProjectOverviewHealth
    upcoming_milestones: list[ProjectOverviewTaskSummary] = Field(default_factory=list)
    top_risks: list[ProjectOverviewRiskSummary] = Field(default_factory=list)
    top_issues: list[ProjectOverviewIssueSummary] = Field(default_factory=list)
    pending_decisions: list[ProjectOverviewDecisionSummary] = Field(default_factory=list)
    recent_activity: list[ProjectOverviewActivitySummary] = Field(default_factory=list)
    resource_summary: ProjectOverviewResourceSummary