from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkItemBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    status_id: UUID | None = None
    color: str | None = Field(default=None, max_length=50)
    start_date: date | None = None
    target_end_date: date | None = None


class WorkItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status_id: UUID | None = None
    color: str | None = Field(default=None, max_length=50)
    start_date: date | None = None
    target_end_date: date | None = None


class PortfolioCreate(WorkItemBase):
    pass


class PortfolioUpdate(WorkItemUpdate):
    pass


class ProgramCreate(WorkItemBase):
    pass


class ProgramUpdate(WorkItemUpdate):
    pass


class ProjectCreate(WorkItemBase):
    portfolio_id: UUID | None = None


class ProjectUpdate(WorkItemUpdate):
    pass


class StatusSummary(BaseModel):
    id: UUID
    label: str
    value: str
    color: str | None

    model_config = ConfigDict(from_attributes=True)


class PortfolioRead(BaseModel):
    id: UUID
    account_id: UUID
    name: str
    description: str | None
    status_id: UUID | None
    color: str | None
    start_date: date | None
    target_end_date: date | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProgramRead(BaseModel):
    id: UUID
    account_id: UUID
    portfolio_id: UUID
    name: str
    description: str | None
    status_id: UUID | None
    color: str | None
    start_date: date | None
    target_end_date: date | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectRead(BaseModel):
    id: UUID
    account_id: UUID
    portfolio_id: UUID
    program_id: UUID
    name: str
    description: str | None
    status_id: UUID | None
    color: str | None
    start_date: date | None
    target_end_date: date | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SidebarNode(BaseModel):
    id: UUID
    name: str
    status: StatusSummary | None
    color: str | None


class SidebarProject(SidebarNode):
    pass


class SidebarProgram(SidebarNode):
    projects: list[SidebarProject]


class SidebarPortfolio(SidebarNode):
    programs: list[SidebarProgram]


class AccountSidebarRead(BaseModel):
    portfolios: list[SidebarPortfolio]


class PortfolioOverviewRead(BaseModel):
    portfolio: PortfolioRead
    program_count: int
    project_count: int


class ProgramOverviewRead(BaseModel):
    program: ProgramRead
    project_count: int