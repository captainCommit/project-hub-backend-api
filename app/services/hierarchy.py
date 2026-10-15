from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.models.access_grant import ProjectModule
from app.models.account_member import AccountMemberRole
from app.models.option_value import OptionValue
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.hierarchy import HierarchyRepository
from app.schemas.hierarchy import (
    ProgramsProjectsRead,
    PortfolioCreate,
    PortfolioUpdate,
    ProgramCreate,
    ProgramUpdate,
    ProjectCreate,
    ProjectUpdate,
)
from app.services.access import AccessScope, access_for


# Portfolios sit at the top of the account, so only owners create them; below that, managers of the parent do.
PORTFOLIO_CREATE_ROLES = {AccountMemberRole.OWNER.value}
FINANCIAL_PROJECT_FIELDS = {"budget"}


class HierarchyService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.hierarchy = HierarchyRepository(db)

    def list_portfolios(self, *, account_id: UUID, current_user: User) -> list[Portfolio]:
        scope = access_for(self.db, account_id=account_id, user=current_user)
        return scope.keep_visible(self.hierarchy.list_portfolios_for_account(account_id), scope.visible_portfolio_ids())

    def create_portfolio(
        self,
        *,
        account_id: UUID,
        portfolio_in: PortfolioCreate,
        current_user: User,
    ) -> Portfolio:
        self.require_account_role(
            account_id=account_id,
            user_id=current_user.id,
            allowed_roles=PORTFOLIO_CREATE_ROLES,
        )
        access_for(self.db, account_id=account_id, user=current_user).require_writable()
        status_id = self.resolve_status_id(
            account_id=account_id,
            entity_type="PORTFOLIO",
            status_id=portfolio_in.status_id,
        )
        portfolio = self.hierarchy.create_portfolio(
            account_id=account_id,
            name=portfolio_in.name,
            description=portfolio_in.description,
            status_id=status_id,
            color=portfolio_in.color,
            start_date=portfolio_in.start_date,
            target_end_date=portfolio_in.target_end_date,
            created_by=current_user.id,
        )
        self.db.commit()
        self.db.refresh(portfolio)
        return portfolio

    def get_portfolio(self, *, portfolio_id: UUID, current_user: User) -> Portfolio:
        portfolio = self.get_portfolio_or_404(portfolio_id)
        access_for(self.db, account_id=portfolio.account_id, user=current_user).require_visible_portfolio(portfolio)
        return portfolio

    def update_portfolio(
        self,
        *,
        portfolio_id: UUID,
        portfolio_in: PortfolioUpdate,
        current_user: User,
    ) -> Portfolio:
        portfolio = self.get_portfolio_or_404(portfolio_id)
        access_for(self.db, account_id=portfolio.account_id, user=current_user).require_portfolio(portfolio, manage=True)
        changes = portfolio_in.model_dump(exclude_unset=True)
        if "status_id" in changes and changes["status_id"] is not None:
            changes["status_id"] = self.validate_status_id(
                account_id=portfolio.account_id,
                entity_type="PORTFOLIO",
                status_id=changes["status_id"],
            )
        portfolio = self.hierarchy.update_portfolio(portfolio, changes)
        self.db.commit()
        self.db.refresh(portfolio)
        return portfolio

    def list_programs(self, *, portfolio_id: UUID, current_user: User) -> list[Program]:
        portfolio = self.get_portfolio_or_404(portfolio_id)
        scope = access_for(self.db, account_id=portfolio.account_id, user=current_user)
        scope.require_visible_portfolio(portfolio)
        return scope.keep_visible(self.hierarchy.list_programs_for_portfolio(portfolio_id), scope.visible_program_ids())

    def create_program(
        self,
        *,
        portfolio_id: UUID,
        program_in: ProgramCreate,
        current_user: User,
    ) -> Program:
        portfolio = self.get_portfolio_or_404(portfolio_id)
        access_for(self.db, account_id=portfolio.account_id, user=current_user).require_portfolio(portfolio, manage=True)
        status_id = self.resolve_status_id(
            account_id=portfolio.account_id,
            entity_type="PROGRAM",
            status_id=program_in.status_id,
        )
        program = self.hierarchy.create_program(
            account_id=portfolio.account_id,
            portfolio_id=portfolio.id,
            name=program_in.name,
            description=program_in.description,
            status_id=status_id,
            color=program_in.color,
            start_date=program_in.start_date,
            target_end_date=program_in.target_end_date,
            created_by=current_user.id,
        )
        self.db.commit()
        self.db.refresh(program)
        return program

    def get_program(self, *, program_id: UUID, current_user: User) -> Program:
        program = self.get_program_or_404(program_id)
        access_for(self.db, account_id=program.account_id, user=current_user).require_visible_program(program)
        return program

    def update_program(
        self,
        *,
        program_id: UUID,
        program_in: ProgramUpdate,
        current_user: User,
    ) -> Program:
        program = self.get_program_or_404(program_id)
        access_for(self.db, account_id=program.account_id, user=current_user).require_program(program, manage=True)
        changes = program_in.model_dump(exclude_unset=True)
        if "status_id" in changes and changes["status_id"] is not None:
            changes["status_id"] = self.validate_status_id(
                account_id=program.account_id,
                entity_type="PROGRAM",
                status_id=changes["status_id"],
            )
        program = self.hierarchy.update_program(program, changes)
        self.db.commit()
        self.db.refresh(program)
        return program

    def list_projects(self, *, program_id: UUID, current_user: User) -> list[Project]:
        program = self.get_program_or_404(program_id)
        scope = access_for(self.db, account_id=program.account_id, user=current_user)
        scope.require_visible_program(program)
        projects = scope.keep_visible(self.hierarchy.list_projects_for_program(program_id), scope.visible_project_ids())
        return self.hide_budgets(scope, projects)

    def create_project(
        self,
        *,
        program_id: UUID,
        project_in: ProjectCreate,
        current_user: User,
    ) -> Project:
        program = self.get_program_or_404(program_id)
        portfolio_id = project_in.portfolio_id or program.portfolio_id
        if portfolio_id != program.portfolio_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Program does not belong to the supplied portfolio.",
            )
        portfolio = self.get_portfolio_or_404(portfolio_id)
        if portfolio.account_id != program.account_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Program, portfolio, and account hierarchy is inconsistent.",
            )
        access_for(self.db, account_id=program.account_id, user=current_user).require_program(program, manage=True)
        status_id = self.resolve_status_id(
            account_id=program.account_id,
            entity_type="PROJECT",
            status_id=project_in.status_id,
        )
        project = self.hierarchy.create_project(
            account_id=program.account_id,
            portfolio_id=portfolio.id,
            program_id=program.id,
            name=project_in.name,
            description=project_in.description,
            delivery_type=project_in.delivery_type.value,
            status_id=status_id,
            color=project_in.color,
            start_date=project_in.start_date,
            target_end_date=project_in.target_end_date,
            budget=project_in.budget,
            project_manager=project_in.project_manager,
            created_by=current_user.id,
        )
        self.db.commit()
        self.db.refresh(project)
        return project

    def get_project(self, *, project_id: UUID, current_user: User) -> Project:
        project = self.get_project_or_404(project_id)
        scope = access_for(self.db, account_id=project.account_id, user=current_user)
        scope.require_project(project)
        return self.hide_budgets(scope, [project])[0]

    def update_project(
        self,
        *,
        project_id: UUID,
        project_in: ProjectUpdate,
        current_user: User,
    ) -> Project:
        project = self.get_project_or_404(project_id)
        changes = project_in.model_dump(exclude_unset=True)
        scope = access_for(self.db, account_id=project.account_id, user=current_user)
        if changes and set(changes) <= FINANCIAL_PROJECT_FIELDS:
            # Members with the Financials module may change the budget; everything else is for managers.
            scope.require_project(project, module=ProjectModule.FINANCIALS)
        else:
            scope.require_project(project, manage=True)
        if "delivery_type" in changes:
            changes["delivery_type"] = changes["delivery_type"].value
        if "status_id" in changes and changes["status_id"] is not None:
            changes["status_id"] = self.validate_status_id(
                account_id=project.account_id,
                entity_type="PROJECT",
                status_id=changes["status_id"],
            )
        project = self.hierarchy.update_project(project, changes)
        self.db.commit()
        self.db.refresh(project)
        return project

    def get_sidebar(self, *, account_id: UUID, current_user: User) -> dict[str, list[dict[str, object]]]:
        portfolios, programs, projects = self.visible_hierarchy(account_id=account_id, current_user=current_user)

        status_ids = {
            item.status_id
            for item in [*portfolios, *programs, *projects]
            if item.status_id is not None
        }
        statuses = self.hierarchy.get_status_values_by_ids(status_ids)

        projects_by_program: dict[UUID, list[Project]] = {}
        for project in projects:
            projects_by_program.setdefault(project.program_id, []).append(project)

        programs_by_portfolio: dict[UUID, list[Program]] = {}
        for program in programs:
            programs_by_portfolio.setdefault(program.portfolio_id, []).append(program)

        return {
            "portfolios": [
                {
                    "id": portfolio.id,
                    "name": portfolio.name,
                    "status": self.status_summary(portfolio.status_id, statuses),
                    "color": portfolio.color,
                    "programs": [
                        {
                            "id": program.id,
                            "name": program.name,
                            "status": self.status_summary(program.status_id, statuses),
                            "color": program.color,
                            "projects": [
                                {
                                    "id": project.id,
                                    "name": project.name,
                                    "status": self.status_summary(project.status_id, statuses),
                                    "color": project.color,
                                }
                                for project in projects_by_program.get(program.id, [])
                            ],
                        }
                        for program in programs_by_portfolio.get(portfolio.id, [])
                    ],
                }
                for portfolio in portfolios
            ]
        }

    def get_programs_projects(self, *, account_id: UUID, current_user: User) -> dict[str, list[dict[str, object]]]:
        portfolios, programs, projects = self.visible_hierarchy(account_id=account_id, current_user=current_user)

        status_ids = {
            item.status_id
            for item in [*portfolios, *programs, *projects]
            if item.status_id is not None
        }
        statuses = self.hierarchy.get_status_values_by_ids(status_ids)

        projects_by_program: dict[UUID, list[Project]] = {}
        for project in projects:
            projects_by_program.setdefault(project.program_id, []).append(project)

        programs_by_portfolio: dict[UUID, list[Program]] = {}
        for program in programs:
            programs_by_portfolio.setdefault(program.portfolio_id, []).append(program)

        return {
            "portfolios": [
                {
                    "id": portfolio.id,
                    "name": portfolio.name,
                    "status": self.status_summary(portfolio.status_id, statuses),
                    "description": portfolio.description,
                    "color": portfolio.color,
                    "start_date": portfolio.start_date,
                    "target_end_date": portfolio.target_end_date,
                    "created_at": portfolio.created_at,
                    "programs": [
                        {
                            "id": program.id,
                            "name": program.name,
                            "status": self.status_summary(program.status_id, statuses),
                            "project_count": len(projects_by_program.get(program.id, [])),
                            "portfolio_id": program.portfolio_id,
                            "description": program.description,
                            "color": program.color,
                            "start_date": program.start_date,
                            "target_end_date": program.target_end_date,
                            "created_at": program.created_at,
                            "projects": [
                                {
                                    "id": project.id,
                                    "name": project.name,
                                    "status": self.status_summary(project.status_id, statuses),
                                    "delivery_type": project.delivery_type,
                                    "start_date": project.start_date,
                                    "target_end_date": project.target_end_date,
                                    "program_id": project.program_id,
                                    "portfolio_id": project.portfolio_id,
                                    "description": project.description,
                                    "color": project.color,
                                    "budget": project.budget,
                                    "created_at": project.created_at,
                                }
                                for project in projects_by_program.get(program.id, [])
                            ],
                        }
                        for program in programs_by_portfolio.get(portfolio.id, [])
                    ],
                }
                for portfolio in portfolios
            ]
        }

    def get_portfolio_overview(self, *, portfolio_id: UUID, current_user: User) -> dict[str, object]:
        portfolio = self.get_portfolio_or_404(portfolio_id)
        scope = access_for(self.db, account_id=portfolio.account_id, user=current_user)
        scope.require_visible_portfolio(portfolio)
        if scope.is_owner:
            program_count = self.hierarchy.count_programs_for_portfolio(portfolio_id)
            project_count = self.hierarchy.count_projects_for_portfolio(portfolio_id)
        else:
            programs = self.hierarchy.list_programs_for_portfolio(portfolio_id)
            projects = self.hierarchy.list_projects_for_portfolio(portfolio_id)
            program_count = len(scope.keep_visible(programs, scope.visible_program_ids()))
            project_count = len(scope.keep_visible(projects, scope.visible_project_ids()))
        return {"portfolio": portfolio, "program_count": program_count, "project_count": project_count}

    def get_program_overview(self, *, program_id: UUID, current_user: User) -> dict[str, object]:
        program = self.get_program_or_404(program_id)
        scope = access_for(self.db, account_id=program.account_id, user=current_user)
        scope.require_visible_program(program)
        projects = scope.keep_visible(self.hierarchy.list_projects_for_program(program_id), scope.visible_project_ids())
        return {"program": program, "project_count": len(projects)}

    def visible_hierarchy(
        self, *, account_id: UUID, current_user: User
    ) -> tuple[list[Portfolio], list[Program], list[Project]]:
        scope = access_for(self.db, account_id=account_id, user=current_user)
        return (
            scope.keep_visible(self.hierarchy.list_portfolios_for_account(account_id), scope.visible_portfolio_ids()),
            scope.keep_visible(self.hierarchy.list_programs_for_account(account_id), scope.visible_program_ids()),
            self.hide_budgets(
                scope, scope.keep_visible(self.hierarchy.list_projects_for_account(account_id), scope.visible_project_ids())
            ),
        )

    def hide_budgets(self, scope: AccessScope, projects: list[Project]) -> list[Project]:
        """Blank the budget of projects whose Financials this user can't see (for this request only, never saved)."""
        visible = scope.visible_project_ids(ProjectModule.FINANCIALS)
        if visible is not None:
            for project in projects:
                if project.id not in visible:
                    set_committed_value(project, "budget", None)
        return projects

    def resolve_status_id(
        self,
        *,
        account_id: UUID,
        entity_type: str,
        status_id: UUID | None,
    ) -> UUID | None:
        if status_id is None:
            return self.hierarchy.get_default_status_id(account_id=account_id, entity_type=entity_type)
        return self.validate_status_id(account_id=account_id, entity_type=entity_type, status_id=status_id)

    def validate_status_id(self, *, account_id: UUID, entity_type: str, status_id: UUID) -> UUID:
        status_value = self.hierarchy.get_valid_status(
            account_id=account_id,
            entity_type=entity_type,
            status_id=status_id,
        )
        if status_value is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid {entity_type.lower()} status.",
            )
        return status_value.id

    def status_summary(
        self,
        status_id: UUID | None,
        statuses: dict[UUID, OptionValue],
    ) -> dict[str, object] | None:
        if status_id is None or status_id not in statuses:
            return None
        status_value = statuses[status_id]
        return {
            "id": status_value.id,
            "label": status_value.label,
            "value": status_value.value,
            "color": status_value.color,
        }

    def get_portfolio_or_404(self, portfolio_id: UUID) -> Portfolio:
        portfolio = self.hierarchy.get_portfolio(portfolio_id)
        if portfolio is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found.")
        return portfolio

    def get_program_or_404(self, program_id: UUID) -> Program:
        program = self.hierarchy.get_program(program_id)
        if program is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Program not found.")
        return program

    def get_project_or_404(self, project_id: UUID) -> Project:
        project = self.hierarchy.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
        return project

    def require_account_member(self, *, account_id: UUID, user_id: UUID) -> None:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")

    def require_account_role(
        self,
        *,
        account_id: UUID,
        user_id: UUID,
        allowed_roles: set[str],
    ) -> None:
        self.require_account_member(account_id=account_id, user_id=user_id)
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None or membership.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")
