from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.hierarchy import StatusSummary


class RiskCreate(BaseModel):
    title: str = Field(min_length=1)
    cause: str | None = None
    effect: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    assigned_to: UUID | None = None
    target_resolution_date: date | None = None


class RiskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    cause: str | None = None
    effect: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    assigned_to: UUID | None = None
    target_resolution_date: date | None = None


class RiskRead(BaseModel):
    id: UUID
    account_id: UUID
    project_id: UUID
    program_id: UUID | None
    risk_number: str
    title: str
    cause: str | None
    effect: str | None
    priority_id: UUID | None
    status_id: UUID | None
    assigned_to: UUID | None
    target_resolution_date: date | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    priority: StatusSummary | None = None
    status: StatusSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class IssueCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    assigned_to: UUID | None = None
    target_resolution_date: date | None = None


class IssueUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    assigned_to: UUID | None = None
    target_resolution_date: date | None = None


class IssueRead(BaseModel):
    id: UUID
    account_id: UUID
    project_id: UUID
    program_id: UUID | None
    issue_number: str
    title: str
    description: str | None
    priority_id: UUID | None
    status_id: UUID | None
    assigned_to: UUID | None
    target_resolution_date: date | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    priority: StatusSummary | None = None
    status: StatusSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class AssumptionCreate(BaseModel):
    description: str = Field(min_length=1)
    status_id: UUID | None = None
    entered_by: UUID | None = None
    date_entered: date | None = None


class AssumptionUpdate(BaseModel):
    description: str | None = Field(default=None, min_length=1)
    status_id: UUID | None = None
    entered_by: UUID | None = None
    date_entered: date | None = None


class AssumptionRead(BaseModel):
    id: UUID
    account_id: UUID
    project_id: UUID
    program_id: UUID | None
    assumption_number: str
    description: str
    status_id: UUID | None
    entered_by: UUID | None
    date_entered: date | None
    created_at: datetime
    updated_at: datetime
    status: StatusSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class DecisionCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str | None = None
    impact: str | None = None
    status_id: UUID | None = None
    proposed_date: date | None = None
    approved_date: date | None = None
    approved_by: UUID | None = None


class DecisionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    impact: str | None = None
    status_id: UUID | None = None
    proposed_date: date | None = None
    approved_date: date | None = None
    approved_by: UUID | None = None


class DecisionRead(BaseModel):
    id: UUID
    account_id: UUID
    project_id: UUID
    program_id: UUID | None
    decision_number: str
    title: str
    description: str | None
    impact: str | None
    status_id: UUID | None
    proposed_date: date | None
    approved_date: date | None
    approved_by: UUID | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    status: StatusSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class DecisionOptionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    pros: str | None = None
    cons: str | None = None
    work_effort: str | None = Field(default=None, max_length=255)
    sort_order: int = 0


class DecisionOptionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    pros: str | None = None
    cons: str | None = None
    work_effort: str | None = Field(default=None, max_length=255)
    sort_order: int | None = None


class DecisionOptionRead(BaseModel):
    id: UUID
    account_id: UUID
    decision_id: UUID
    title: str | None
    pros: str | None
    cons: str | None
    work_effort: str | None
    sort_order: int
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)