from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.hierarchy import StatusSummary
from app.schemas.raid import OptionKeyInput, RaidProgramSummary, RaidProjectSummary


class ProjectLogReadBase(BaseModel):
    id: UUID
    account_id: UUID
    project_id: UUID
    program_id: UUID | None
    title: str
    description: str | None
    priority_id: UUID | None
    status_id: UUID | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    created_by_name: str | None = None
    comment_count: int = 0
    portfolio_id: UUID | None = None
    priority: StatusSummary | None = None
    status: StatusSummary | None = None
    project: RaidProjectSummary | None = None
    program: RaidProgramSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class DependencyCreate(OptionKeyInput):
    title: str = Field(min_length=1)
    description: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    impact_of_delay: str | None = None
    fallback_plan: str | None = None
    assigned_to_name: str | None = Field(default=None, max_length=255)
    entered_by_name: str | None = Field(default=None, max_length=255)
    date_entered: date | None = None


class DependencyUpdate(OptionKeyInput):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    impact_of_delay: str | None = None
    fallback_plan: str | None = None
    assigned_to_name: str | None = Field(default=None, max_length=255)
    entered_by_name: str | None = Field(default=None, max_length=255)
    date_entered: date | None = None


class DependencyRead(ProjectLogReadBase):
    dependency_number: str
    impact_of_delay: str | None
    fallback_plan: str | None
    assigned_to_name: str | None
    entered_by_name: str | None
    date_entered: date | None


class ChangeRequestCreate(OptionKeyInput):
    title: str = Field(min_length=1)
    description: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    proposed_by_name: str | None = Field(default=None, max_length=255)
    proposed_date: date | None = None
    related_record: str | None = Field(default=None, max_length=255)


class ChangeRequestUpdate(OptionKeyInput):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    proposed_by_name: str | None = Field(default=None, max_length=255)
    proposed_date: date | None = None
    related_record: str | None = Field(default=None, max_length=255)


class ChangeRequestRead(ProjectLogReadBase):
    change_request_number: str
    proposed_by_name: str | None
    proposed_date: date | None
    related_record: str | None


class RequirementCreate(OptionKeyInput):
    title: str = Field(min_length=1)
    description: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    entered_by_name: str | None = Field(default=None, max_length=255)
    entered_date: date | None = None


class RequirementUpdate(OptionKeyInput):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    entered_by_name: str | None = Field(default=None, max_length=255)
    entered_date: date | None = None


class RequirementRead(ProjectLogReadBase):
    requirement_number: str
    entered_by_name: str | None
    entered_date: date | None
    test_case_ids: list[UUID] = []
    test_case_numbers: list[str] = []


TestCaseResult = Literal["NOT_EXECUTED", "PASS", "FAIL"]


class TestCaseFields(OptionKeyInput):
    description: str | None = None
    priority_id: UUID | None = None
    status_id: UUID | None = None
    sprint_id: UUID | None = None
    requirement_id: UUID | None = None
    designed_by_name: str | None = Field(default=None, max_length=255)
    executed_by_name: str | None = Field(default=None, max_length=255)
    execution_date: date | None = None
    dependencies: str | None = None
    test_steps: str | None = None
    conditions: str | None = None
    test_input: str | None = None
    expected_results: str | None = None
    actual_results: str | None = None
    notes: str | None = None

    @field_validator("result", mode="before", check_fields=False)
    @classmethod
    def normalize_result(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class TestCaseCreate(TestCaseFields):
    title: str = Field(min_length=1)
    result: TestCaseResult = "NOT_EXECUTED"


class TestCaseUpdate(TestCaseFields):
    title: str | None = Field(default=None, min_length=1)
    result: TestCaseResult | None = None


class TestCaseRead(ProjectLogReadBase):
    test_case_number: str
    sprint_id: UUID | None
    requirement_id: UUID | None
    designed_by_name: str | None
    executed_by_name: str | None
    execution_date: date | None
    result: str
    dependencies: str | None
    test_steps: str | None
    conditions: str | None
    test_input: str | None
    expected_results: str | None
    actual_results: str | None
    notes: str | None
    requirement_number: str | None = None
    sprint_name: str | None = None
