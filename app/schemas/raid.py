from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.hierarchy import StatusSummary


WorkEffortUnit = Literal["HOURS", "DAYS", "WEEKS"]


class RaidProjectSummary(BaseModel):
    id: UUID
    name: str

    model_config = ConfigDict(from_attributes=True)


class RaidProgramSummary(BaseModel):
    id: UUID
    name: str

    model_config = ConfigDict(from_attributes=True)


class StatusKeyInput(BaseModel):
    """Lets clients pass an option value key (e.g. "open") instead of an option value id."""

    status: str | None = Field(default=None, max_length=100)


class OptionKeyInput(StatusKeyInput):
    priority: str | None = Field(default=None, max_length=100)


class RiskCreate(OptionKeyInput):
    title: str = Field(min_length=1)
    cause: str | None = None
    effect: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    assigned_to: UUID | None = None
    assigned_to_name: str | None = Field(default=None, max_length=255)
    target_resolution_date: date | None = None


class RiskUpdate(OptionKeyInput):
    title: str | None = Field(default=None, min_length=1)
    cause: str | None = None
    effect: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    assigned_to: UUID | None = None
    assigned_to_name: str | None = Field(default=None, max_length=255)
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
    assigned_to_name: str | None = Field(default=None, max_length=255)
    target_resolution_date: date | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    created_by_name: str | None = None
    comment_count: int = 0
    portfolio_id: UUID | None = None
    priority: StatusSummary | None = None
    status: StatusSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class AccountRiskRead(RiskRead):
    project: RaidProjectSummary | None = None
    program: RaidProgramSummary | None = None


class IssueCreate(OptionKeyInput):
    title: str = Field(min_length=1)
    description: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    assigned_to: UUID | None = None
    assigned_to_name: str | None = Field(default=None, max_length=255)
    target_resolution_date: date | None = None


class IssueUpdate(OptionKeyInput):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    assigned_to: UUID | None = None
    assigned_to_name: str | None = Field(default=None, max_length=255)
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
    assigned_to_name: str | None = Field(default=None, max_length=255)
    target_resolution_date: date | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    created_by_name: str | None = None
    comment_count: int = 0
    portfolio_id: UUID | None = None
    priority: StatusSummary | None = None
    status: StatusSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class AccountIssueRead(IssueRead):
    project: RaidProjectSummary | None = None
    program: RaidProgramSummary | None = None


class AssumptionCreate(OptionKeyInput):
    description: str = Field(min_length=1)
    priority_id: UUID | None = None
    status_id: UUID | None = None
    entered_by: UUID | None = None
    entered_by_name: str | None = Field(default=None, max_length=255)
    date_entered: date | None = None
    assigned_to_name: str | None = Field(default=None, max_length=255)
    target_date: date | None = None


class AssumptionUpdate(OptionKeyInput):
    description: str | None = Field(default=None, min_length=1)
    priority_id: UUID | None = None
    status_id: UUID | None = None
    entered_by: UUID | None = None
    entered_by_name: str | None = Field(default=None, max_length=255)
    date_entered: date | None = None
    assigned_to_name: str | None = Field(default=None, max_length=255)
    target_date: date | None = None


class AssumptionRead(BaseModel):
    id: UUID
    account_id: UUID
    project_id: UUID
    program_id: UUID | None
    assumption_number: str
    description: str
    priority_id: UUID | None = None
    status_id: UUID | None
    entered_by: UUID | None
    entered_by_name: str | None = None
    date_entered: date | None
    assigned_to_name: str | None = None
    target_date: date | None = None
    created_at: datetime
    updated_at: datetime
    created_by_name: str | None = None
    comment_count: int = 0
    portfolio_id: UUID | None = None
    priority: StatusSummary | None = None
    status: StatusSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class AccountAssumptionRead(AssumptionRead):
    project: RaidProjectSummary | None = None
    program: RaidProgramSummary | None = None


class DecisionCreate(OptionKeyInput):
    title: str = Field(min_length=1)
    description: str | None = None
    impact: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    proposed_date: date | None = None
    proposed_by_name: str | None = Field(default=None, max_length=255)
    related_record: str | None = Field(default=None, max_length=255)
    approved_date: date | None = None
    approved_by: UUID | None = None


class DecisionUpdate(OptionKeyInput):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    impact: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    proposed_date: date | None = None
    proposed_by_name: str | None = Field(default=None, max_length=255)
    related_record: str | None = Field(default=None, max_length=255)
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
    priority_id: UUID | None = None
    status_id: UUID | None
    proposed_date: date | None
    proposed_by_name: str | None = None
    related_record: str | None = None
    approved_date: date | None
    approved_by: UUID | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    created_by_name: str | None = None
    comment_count: int = 0
    portfolio_id: UUID | None = None
    priority: StatusSummary | None = None
    status: StatusSummary | None = None
    # The first option with an effort, for the decision log's Work Effort column.
    work_effort: str | None = None
    work_effort_value: Decimal | None = None
    work_effort_unit: WorkEffortUnit | None = None

    model_config = ConfigDict(from_attributes=True)


class AccountDecisionRead(DecisionRead):
    project: RaidProjectSummary | None = None
    program: RaidProgramSummary | None = None


class DecisionOptionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    pros: str | None = None
    cons: str | None = None
    work_effort: str | None = Field(default=None, max_length=255)
    work_effort_value: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    work_effort_unit: WorkEffortUnit | None = None
    implementation_resources: list[str] | None = Field(default=None, max_length=50)
    sort_order: int = 0


class DecisionOptionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    pros: str | None = None
    cons: str | None = None
    work_effort: str | None = Field(default=None, max_length=255)
    work_effort_value: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    work_effort_unit: WorkEffortUnit | None = None
    implementation_resources: list[str] | None = Field(default=None, max_length=50)
    sort_order: int | None = None


class DecisionOptionRead(BaseModel):
    id: UUID
    account_id: UUID
    decision_id: UUID
    title: str | None
    pros: str | None
    cons: str | None
    work_effort: str | None
    work_effort_value: Decimal | None = None
    work_effort_unit: WorkEffortUnit | None = None
    implementation_resources: list[str] = []
    sort_order: int
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    @field_validator("implementation_resources", mode="before")
    @classmethod
    def none_as_empty(cls, value: object) -> object:
        return value or []

    model_config = ConfigDict(from_attributes=True)