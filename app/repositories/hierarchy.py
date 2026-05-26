from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project


class HierarchyRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_portfolio(self, **values: object) -> Portfolio:
        portfolio = Portfolio(**values)
        self.db.add(portfolio)
        self.db.flush()
        self.db.refresh(portfolio)
        return portfolio

    def get_portfolio(self, portfolio_id: UUID) -> Portfolio | None:
        return self.db.get(Portfolio, portfolio_id)

    def list_portfolios_for_account(self, account_id: UUID) -> list[Portfolio]:
        statement = (
            select(Portfolio)
            .where(Portfolio.account_id == account_id)
            .order_by(Portfolio.name)
        )
        return list(self.db.scalars(statement).all())

    def update_portfolio(self, portfolio: Portfolio, changes: dict[str, object]) -> Portfolio:
        for field, value in changes.items():
            setattr(portfolio, field, value)
        self.db.add(portfolio)
        self.db.flush()
        self.db.refresh(portfolio)
        return portfolio

    def create_program(self, **values: object) -> Program:
        program = Program(**values)
        self.db.add(program)
        self.db.flush()
        self.db.refresh(program)
        return program

    def get_program(self, program_id: UUID) -> Program | None:
        return self.db.get(Program, program_id)

    def list_programs_for_portfolio(self, portfolio_id: UUID) -> list[Program]:
        statement = (
            select(Program)
            .where(Program.portfolio_id == portfolio_id)
            .order_by(Program.name)
        )
        return list(self.db.scalars(statement).all())

    def list_programs_for_account(self, account_id: UUID) -> list[Program]:
        statement = (
            select(Program)
            .where(Program.account_id == account_id)
            .order_by(Program.name)
        )
        return list(self.db.scalars(statement).all())

    def update_program(self, program: Program, changes: dict[str, object]) -> Program:
        for field, value in changes.items():
            setattr(program, field, value)
        self.db.add(program)
        self.db.flush()
        self.db.refresh(program)
        return program

    def create_project(self, **values: object) -> Project:
        project = Project(**values)
        self.db.add(project)
        self.db.flush()
        self.db.refresh(project)
        return project

    def get_project(self, project_id: UUID) -> Project | None:
        return self.db.get(Project, project_id)

    def list_projects_for_program(self, program_id: UUID) -> list[Project]:
        statement = (
            select(Project)
            .where(Project.program_id == program_id)
            .order_by(Project.name)
        )
        return list(self.db.scalars(statement).all())

    def list_projects_for_account(self, account_id: UUID) -> list[Project]:
        statement = (
            select(Project)
            .where(Project.account_id == account_id)
            .order_by(Project.name)
        )
        return list(self.db.scalars(statement).all())

    def list_projects_for_portfolio(self, portfolio_id: UUID) -> list[Project]:
        statement = (
            select(Project)
            .where(Project.portfolio_id == portfolio_id)
            .order_by(Project.name)
        )
        return list(self.db.scalars(statement).all())

    def update_project(self, project: Project, changes: dict[str, object]) -> Project:
        for field, value in changes.items():
            setattr(project, field, value)
        self.db.add(project)
        self.db.flush()
        self.db.refresh(project)
        return project

    def get_valid_status(
        self,
        *,
        account_id: UUID,
        entity_type: str,
        status_id: UUID,
    ) -> OptionValue | None:
        statement = (
            select(OptionValue)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionValue.id == status_id,
                OptionSet.account_id == account_id,
                OptionSet.entity_type == entity_type,
                OptionSet.name == "STATUS",
                OptionValue.is_active.is_(True),
            )
        )
        return self.db.scalar(statement)

    def get_default_status_id(self, *, account_id: UUID, entity_type: str) -> UUID | None:
        statement = (
            select(OptionValue.id)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionSet.account_id == account_id,
                OptionSet.entity_type == entity_type,
                OptionSet.name == "STATUS",
                OptionValue.is_active.is_(True),
                OptionValue.is_default.is_(True),
            )
            .order_by(OptionValue.sort_order, OptionValue.label)
            .limit(1)
        )
        return self.db.scalar(statement)

    def get_status_values_by_ids(self, status_ids: Iterable[UUID]) -> dict[UUID, OptionValue]:
        status_ids = list(status_ids)
        if not status_ids:
            return {}
        statement = select(OptionValue).where(OptionValue.id.in_(status_ids))
        return {status.id: status for status in self.db.scalars(statement).all()}

    def count_programs_for_portfolio(self, portfolio_id: UUID) -> int:
        statement = select(func.count()).select_from(Program).where(Program.portfolio_id == portfolio_id)
        return int(self.db.scalar(statement) or 0)

    def count_projects_for_portfolio(self, portfolio_id: UUID) -> int:
        statement = select(func.count()).select_from(Project).where(Project.portfolio_id == portfolio_id)
        return int(self.db.scalar(statement) or 0)

    def count_projects_for_program(self, program_id: UUID) -> int:
        statement = select(func.count()).select_from(Project).where(Project.program_id == program_id)
        return int(self.db.scalar(statement) or 0)