from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.hierarchy import StatusSummary


HealthStatus = Literal["GREEN", "YELLOW", "RED", "UNKNOWN"]
HealthTrend = Literal["IMPROVING", "STABLE", "DECLINING", "UNKNOWN"]


class AccountDashboardSummary(BaseModel):
    portfolio_count: int
    program_count: int
    project_count: int
    active_project_count: int
    completed_project_count: int
    at_risk_project_count: int
    total_tasks: int
    completed_tasks: int
    overdue_tasks: int
    open_risks: int
    open_issues: int
    pending_decisions: int
    overallocated_resources: int


class AccountDashboardHealth(BaseModel):
    overall: HealthStatus
    schedule: HealthStatus
    scope: HealthStatus
    resources: HealthStatus
    trend: HealthTrend


class DashboardProgramSummary(BaseModel):
    id: UUID
    name: str


class DashboardProjectMiniSummary(BaseModel):
    id: UUID
    name: str


class DashboardProjectAtRiskSummary(BaseModel):
    id: UUID
    account_id: UUID
    portfolio_id: UUID
    program_id: UUID
    name: str
    status: StatusSummary | None
    start_date: date | None
    target_end_date: date | None
    health: AccountDashboardHealth
    overdue_tasks: int
    open_risks: int
    open_issues: int
    overallocated_resources: int


class DashboardRiskSummary(BaseModel):
    id: UUID
    project_id: UUID
    program_id: UUID
    risk_number: str
    title: str
    priority: StatusSummary | None
    status: StatusSummary | None
    target_resolution_date: date | None
    created_at: datetime
    project: DashboardProjectMiniSummary
    program: DashboardProgramSummary


class DashboardIssueSummary(BaseModel):
    id: UUID
    project_id: UUID
    program_id: UUID
    issue_number: str
    title: str
    priority: StatusSummary | None
    status: StatusSummary | None
    target_resolution_date: date | None
    created_at: datetime
    project: DashboardProjectMiniSummary
    program: DashboardProgramSummary


class DashboardTaskSummary(BaseModel):
    id: UUID
    project_id: UUID
    program_id: UUID
    name: str
    status: StatusSummary | None
    start_date: date | None
    finish_date: date
    project: DashboardProjectMiniSummary
    program: DashboardProgramSummary


class DashboardResourceSummary(BaseModel):
    id: UUID
    user_id: UUID | None = None
    name: str
    role: str | None = None
    weekly_capacity_hours: Decimal


class DashboardResourceUtilizationSummary(BaseModel):
    resource: DashboardResourceSummary
    allocated_hours: Decimal
    utilization_percent: float
    overallocated: bool
    project_count: int


class DashboardActivitySummary(BaseModel):
    id: UUID
    entity_type: str
    entity_id: UUID
    action: str
    created_by: UUID | None
    created_at: datetime


class AccountDashboardRead(BaseModel):
    summary: AccountDashboardSummary
    health: AccountDashboardHealth
    projects_at_risk: list[DashboardProjectAtRiskSummary] = Field(default_factory=list)
    top_risks: list[DashboardRiskSummary] = Field(default_factory=list)
    top_issues: list[DashboardIssueSummary] = Field(default_factory=list)
    overdue_tasks: list[DashboardTaskSummary] = Field(default_factory=list)
    resource_utilization: list[DashboardResourceUtilizationSummary] = Field(default_factory=list)
    recent_activity: list[DashboardActivitySummary] = Field(default_factory=list)