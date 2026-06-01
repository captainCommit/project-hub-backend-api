from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog
from app.models.account_member import AccountMemberRole
from app.models.issue import Issue
from app.models.option_value import OptionValue
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.risk import Risk
from app.models.task import Task
from app.models.user import User
from app.repositories.account_dashboard import (
    AccountDashboardRepository,
    DashboardIssueRow,
    DashboardProjectResourceRollupRow,
    DashboardResourceUtilizationRow,
    DashboardRiskRow,
    DashboardTaskRow,
)
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.hierarchy import HierarchyRepository
from app.services.project_overview import ProjectOverviewService


PROJECT_ACTIVE_KEYS = {"active", "inprogress", "doing"}
PROJECT_COMPLETED_KEYS = {"complete", "completed", "done", "closed"}
PROJECT_AT_RISK_KEYS = {"atrisk", "risk", "red", "yellow", "blocked", "offtrack"}
DASHBOARD_READ_ROLES = {
    AccountMemberRole.OWNER.value,
    AccountMemberRole.ADMIN.value,
    AccountMemberRole.MANAGER.value,
    AccountMemberRole.MEMBER.value,
    AccountMemberRole.VIEWER.value,
}


@dataclass(frozen=True)
class DashboardScope:
    portfolios: list[Portfolio]
    programs: list[Program]
    projects: list[Project]
    restrict_activity: bool


class AccountDashboardService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.hierarchy = HierarchyRepository(db)
        self.dashboard = AccountDashboardRepository(db)
        self.overview_helpers = ProjectOverviewService(db)

    def get_account_dashboard(
        self,
        *,
        account_id: UUID,
        current_user: User,
        portfolio_id: UUID | None = None,
        program_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> dict[str, object]:
        self.require_account_member(account_id=account_id, user_id=current_user.id)
        scope = self.resolve_scope(
            account_id=account_id,
            portfolio_id=portfolio_id,
            program_id=program_id,
            project_id=project_id,
        )

        today = self.dashboard.today()
        project_ids = [project.id for project in scope.projects]
        project_statuses = self.hierarchy.get_status_values_by_ids(
            {project.status_id for project in scope.projects if project.status_id is not None}
        )
        task_rows = self.dashboard.list_tasks_for_projects(project_ids)
        risk_rows = self.dashboard.list_risks_for_projects(project_ids)
        issue_rows = self.dashboard.list_issues_for_projects(project_ids)
        decision_rows = self.dashboard.list_decisions_for_projects(project_ids)
        resource_rows = self.dashboard.list_resource_utilization_rollups(project_ids)
        project_resource_rows = self.dashboard.list_project_resource_rollups(project_ids)
        recent_activity = self.dashboard.list_recent_activity(
            account_id=account_id,
            portfolio_ids=[portfolio.id for portfolio in scope.portfolios],
            program_ids=[program.id for program in scope.programs],
            project_ids=project_ids,
            restrict_to_scope=scope.restrict_activity,
            limit=10,
        )

        task_stats = self.build_task_stats(task_rows=task_rows, today=today)
        open_risks = [row for row in risk_rows if self.overview_helpers.is_open_status(row[3])]
        open_issues = [row for row in issue_rows if self.overview_helpers.is_open_status(row[3])]
        pending_decisions = [row for row in decision_rows if self.overview_helpers.is_pending_decision_status(row[3])]
        high_priority_open_issues = [row for row in open_issues if self.overview_helpers.is_high_priority(row[4])]
        resource_utilization = [self.resource_utilization_summary(row) for row in resource_rows]
        resource_utilization = sorted(
            resource_utilization,
            key=lambda item: (
                not bool(item["overallocated"]),
                -float(item["utilization_percent"]),
                str(item["resource"]["name"]),  # type: ignore[index]
            ),
        )
        overallocated_resources = sum(1 for item in resource_utilization if item["overallocated"])

        projects_at_risk = self.projects_at_risk(
            projects=scope.projects,
            project_statuses=project_statuses,
            task_rows=task_rows,
            risk_rows=risk_rows,
            issue_rows=issue_rows,
            resource_rows=project_resource_rows,
            today=today,
        )
        health = self.build_dashboard_health(
            task_stats=task_stats,
            open_risks=len(open_risks),
            open_issues=len(open_issues),
            high_priority_open_issues=len(high_priority_open_issues),
            resource_count=len(resource_utilization),
            overallocated_resources=overallocated_resources,
            task_rows=task_rows,
            risk_rows=risk_rows,
            issue_rows=issue_rows,
            today=today,
        )

        summary = {
            "portfolio_count": len(scope.portfolios),
            "program_count": len(scope.programs),
            "project_count": len(scope.projects),
            "active_project_count": sum(
                1
                for project in scope.projects
                if self.is_active_project_status(self.status_for_project(project, project_statuses))
            ),
            "completed_project_count": sum(
                1
                for project in scope.projects
                if self.is_completed_project_status(self.status_for_project(project, project_statuses))
            ),
            "at_risk_project_count": len(projects_at_risk),
            "total_tasks": int(task_stats["total_tasks"]),
            "completed_tasks": int(task_stats["completed_tasks"]),
            "overdue_tasks": int(task_stats["overdue_tasks"]),
            "open_risks": len(open_risks),
            "open_issues": len(open_issues),
            "pending_decisions": len(pending_decisions),
            "overallocated_resources": overallocated_resources,
        }

        return {
            "summary": summary,
            "health": health,
            "projects_at_risk": projects_at_risk,
            "top_risks": [self.risk_summary(row) for row in self.top_risks(open_risks)],
            "top_issues": [self.issue_summary(row) for row in self.top_issues(open_issues)],
            "overdue_tasks": [self.overdue_task_summary(row) for row in self.overdue_tasks(task_rows=task_rows, today=today)],
            "resource_utilization": resource_utilization[:10],
            "recent_activity": [self.activity_summary(activity) for activity in recent_activity],
        }

    def resolve_scope(
        self,
        *,
        account_id: UUID,
        portfolio_id: UUID | None,
        program_id: UUID | None,
        project_id: UUID | None,
    ) -> DashboardScope:
        portfolio = self.validate_portfolio_filter(account_id=account_id, portfolio_id=portfolio_id)
        program = self.validate_program_filter(account_id=account_id, program_id=program_id, portfolio=portfolio)
        project = self.validate_project_filter(
            account_id=account_id,
            project_id=project_id,
            portfolio=portfolio,
            program=program,
        )
        restrict_activity = portfolio_id is not None or program_id is not None or project_id is not None

        if project is not None:
            program = program or self.get_program_or_404(project.program_id)
            portfolio = portfolio or self.get_portfolio_or_404(project.portfolio_id)
            return DashboardScope(
                portfolios=[portfolio],
                programs=[program],
                projects=[project],
                restrict_activity=restrict_activity,
            )
        if program is not None:
            portfolio = portfolio or self.get_portfolio_or_404(program.portfolio_id)
            return DashboardScope(
                portfolios=[portfolio],
                programs=[program],
                projects=self.hierarchy.list_projects_for_program(program.id),
                restrict_activity=restrict_activity,
            )
        if portfolio is not None:
            return DashboardScope(
                portfolios=[portfolio],
                programs=self.hierarchy.list_programs_for_portfolio(portfolio.id),
                projects=self.hierarchy.list_projects_for_portfolio(portfolio.id),
                restrict_activity=restrict_activity,
            )
        return DashboardScope(
            portfolios=self.hierarchy.list_portfolios_for_account(account_id),
            programs=self.hierarchy.list_programs_for_account(account_id),
            projects=self.hierarchy.list_projects_for_account(account_id),
            restrict_activity=restrict_activity,
        )

    def validate_portfolio_filter(self, *, account_id: UUID, portfolio_id: UUID | None) -> Portfolio | None:
        if portfolio_id is None:
            return None
        portfolio = self.get_portfolio_or_404(portfolio_id)
        if portfolio.account_id != account_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Portfolio must belong to the account.")
        return portfolio

    def validate_program_filter(
        self,
        *,
        account_id: UUID,
        program_id: UUID | None,
        portfolio: Portfolio | None,
    ) -> Program | None:
        if program_id is None:
            return None
        program = self.get_program_or_404(program_id)
        if program.account_id != account_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Program must belong to the account.")
        if portfolio is not None and program.portfolio_id != portfolio.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Program must belong to the supplied portfolio.")
        return program

    def validate_project_filter(
        self,
        *,
        account_id: UUID,
        project_id: UUID | None,
        portfolio: Portfolio | None,
        program: Program | None,
    ) -> Project | None:
        if project_id is None:
            return None
        project = self.get_project_or_404(project_id)
        if project.account_id != account_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project must belong to the account.")
        if portfolio is not None and project.portfolio_id != portfolio.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project must belong to the supplied portfolio.")
        if program is not None and project.program_id != program.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project must belong to the supplied program.")
        return project

    def build_task_stats(self, *, task_rows: list[DashboardTaskRow], today: date) -> dict[str, int | bool]:
        overview_rows = [(task, status_value, task_type_value) for task, _project, _program, status_value, task_type_value in task_rows]
        return self.overview_helpers.build_task_stats(task_rows=overview_rows, today=today)

    def build_dashboard_health(
        self,
        *,
        task_stats: dict[str, int | bool],
        open_risks: int,
        open_issues: int,
        high_priority_open_issues: int,
        resource_count: int,
        overallocated_resources: int,
        task_rows: list[DashboardTaskRow] | None = None,
        risk_rows: list[DashboardRiskRow] | None = None,
        issue_rows: list[DashboardIssueRow] | None = None,
        today: date | None = None,
    ) -> dict[str, str]:
        health = self.overview_helpers.build_health(
            stats={
                "total_tasks": int(task_stats["total_tasks"]),
                "completed_tasks": int(task_stats["completed_tasks"]),
                "overdue_tasks": int(task_stats["overdue_tasks"]),
                "upcoming_milestones": int(task_stats["upcoming_milestones"]),
                "open_risks": open_risks,
                "open_issues": open_issues,
                "resource_count": resource_count,
                "overallocated_resources": overallocated_resources,
            },
            has_task_dates=bool(task_stats["has_task_dates"]),
            high_priority_open_issues=high_priority_open_issues,
        )
        health["trend"] = self.dashboard_health_trend(
            task_rows=task_rows or [],
            risk_rows=risk_rows or [],
            issue_rows=issue_rows or [],
            today=today or self.dashboard.today(),
        )
        return health

    def dashboard_health_trend(
        self,
        *,
        task_rows: list[DashboardTaskRow],
        risk_rows: list[DashboardRiskRow],
        issue_rows: list[DashboardIssueRow],
        today: date,
    ) -> str:
        if not task_rows and not risk_rows and not issue_rows:
            return "UNKNOWN"

        recent_window_start = today - timedelta(days=7)
        recent_pressure = 0
        recent_relief = 0

        for task, _project, _program, status_value, _task_type_value in task_rows:
            if task.finish_date is None or task.finish_date >= today:
                continue
            if not self.is_recently_changed(task, recent_window_start=recent_window_start):
                continue
            if self.overview_helpers.is_completed_task_status(status_value):
                recent_relief += 1
            else:
                recent_pressure += 1

        for risk, _project, _program, status_value, priority_value in risk_rows:
            if not self.overview_helpers.is_high_priority(priority_value):
                continue
            if not self.is_recently_changed(risk, recent_window_start=recent_window_start):
                continue
            if self.overview_helpers.is_open_status(status_value):
                recent_pressure += 1
            else:
                recent_relief += 1

        for issue, _project, _program, status_value, priority_value in issue_rows:
            if not self.overview_helpers.is_high_priority(priority_value):
                continue
            if not self.is_recently_changed(issue, recent_window_start=recent_window_start):
                continue
            if self.overview_helpers.is_open_status(status_value):
                recent_pressure += 1
            else:
                recent_relief += 1

        if recent_pressure > recent_relief:
            return "DECLINING"
        if recent_relief > recent_pressure:
            return "IMPROVING"
        return "STABLE"

    def is_recently_changed(self, item: Task | Risk | Issue, *, recent_window_start: date) -> bool:
        updated_at = getattr(item, "updated_at", None)
        created_at = getattr(item, "created_at", None)
        return bool(
            (updated_at is not None and updated_at.date() >= recent_window_start)
            or (created_at is not None and created_at.date() >= recent_window_start)
        )

    def projects_at_risk(
        self,
        *,
        projects: list[Project],
        project_statuses: dict[UUID, OptionValue],
        task_rows: list[DashboardTaskRow],
        risk_rows: list[DashboardRiskRow],
        issue_rows: list[DashboardIssueRow],
        resource_rows: list[DashboardProjectResourceRollupRow],
        today: date,
    ) -> list[dict[str, object]]:
        task_rows_by_project: dict[UUID, list[DashboardTaskRow]] = defaultdict(list)
        for row in task_rows:
            task_rows_by_project[row[0].project_id].append(row)

        risk_rows_by_project: dict[UUID, list[DashboardRiskRow]] = defaultdict(list)
        for row in risk_rows:
            risk_rows_by_project[row[0].project_id].append(row)

        issue_rows_by_project: dict[UUID, list[DashboardIssueRow]] = defaultdict(list)
        for row in issue_rows:
            issue_rows_by_project[row[0].project_id].append(row)

        resource_rows_by_project: dict[UUID, list[DashboardProjectResourceRollupRow]] = defaultdict(list)
        for row in resource_rows:
            resource_rows_by_project[row[0]].append(row)

        project_summaries: list[dict[str, object]] = []
        for project in projects:
            project_task_stats = self.build_task_stats(task_rows=task_rows_by_project.get(project.id, []), today=today)
            open_project_risks = [
                row for row in risk_rows_by_project.get(project.id, []) if self.overview_helpers.is_open_status(row[3])
            ]
            open_project_issues = [
                row for row in issue_rows_by_project.get(project.id, []) if self.overview_helpers.is_open_status(row[3])
            ]
            high_priority_open_project_issues = [
                row for row in open_project_issues if self.overview_helpers.is_high_priority(row[4])
            ]
            project_resource_rows = resource_rows_by_project.get(project.id, [])
            overallocated_project_resources = sum(
                1
                for _project_id, _resource_id, capacity, allocated in project_resource_rows
                if Decimal(str(capacity or 0)) > 0 and Decimal(str(allocated or 0)) > Decimal(str(capacity or 0))
            )
            project_health = self.build_dashboard_health(
                task_stats=project_task_stats,
                open_risks=len(open_project_risks),
                open_issues=len(open_project_issues),
                high_priority_open_issues=len(high_priority_open_project_issues),
                resource_count=len(project_resource_rows),
                overallocated_resources=overallocated_project_resources,
                task_rows=task_rows_by_project.get(project.id, []),
                risk_rows=risk_rows_by_project.get(project.id, []),
                issue_rows=issue_rows_by_project.get(project.id, []),
                today=today,
            )
            project_status = self.status_for_project(project, project_statuses)
            if project_health["overall"] not in {"RED", "YELLOW"} and not self.is_at_risk_project_status(project_status):
                continue
            project_summaries.append(
                {
                    "id": project.id,
                    "account_id": project.account_id,
                    "portfolio_id": project.portfolio_id,
                    "program_id": project.program_id,
                    "name": project.name,
                    "status": self.overview_helpers.option_summary(project_status),
                    "start_date": project.start_date,
                    "target_end_date": project.target_end_date,
                    "health": project_health,
                    "overdue_tasks": int(project_task_stats["overdue_tasks"]),
                    "open_risks": len(open_project_risks),
                    "open_issues": len(open_project_issues),
                    "overallocated_resources": overallocated_project_resources,
                }
            )

        health_rank = {"RED": 0, "YELLOW": 1, "GREEN": 2, "UNKNOWN": 3}
        return sorted(
            project_summaries,
            key=lambda item: (health_rank.get(str(item["health"]["overall"]), 4), str(item["name"]), str(item["id"])),  # type: ignore[index]
        )

    def top_risks(self, risk_rows: list[DashboardRiskRow]) -> list[DashboardRiskRow]:
        return sorted(
            risk_rows,
            key=lambda row: (
                self.overview_helpers.priority_rank(row[4]),
                row[0].target_resolution_date or date.max,
                row[0].created_at,
                row[0].id,
            ),
        )[:10]

    def top_issues(self, issue_rows: list[DashboardIssueRow]) -> list[DashboardIssueRow]:
        return sorted(
            issue_rows,
            key=lambda row: (
                self.overview_helpers.priority_rank(row[4]),
                row[0].target_resolution_date or date.max,
                row[0].created_at,
                row[0].id,
            ),
        )[:10]

    def overdue_tasks(self, *, task_rows: list[DashboardTaskRow], today: date) -> list[DashboardTaskRow]:
        return sorted(
            [
                row
                for row in task_rows
                if row[0].finish_date is not None
                and row[0].finish_date < today
                and not self.overview_helpers.is_completed_task_status(row[3])
            ],
            key=lambda row: (row[0].finish_date, row[0].sort_order, row[0].created_at, row[0].id),
        )[:10]

    def risk_summary(self, row: DashboardRiskRow) -> dict[str, object]:
        risk, project, program, status_value, priority_value = row
        return {
            "id": risk.id,
            "project_id": project.id,
            "program_id": program.id,
            "risk_number": risk.risk_number,
            "title": risk.title,
            "priority": self.overview_helpers.option_summary(priority_value),
            "status": self.overview_helpers.option_summary(status_value),
            "target_resolution_date": risk.target_resolution_date,
            "created_at": risk.created_at,
            "project": {"id": project.id, "name": project.name},
            "program": {"id": program.id, "name": program.name},
        }

    def issue_summary(self, row: DashboardIssueRow) -> dict[str, object]:
        issue, project, program, status_value, priority_value = row
        return {
            "id": issue.id,
            "project_id": project.id,
            "program_id": program.id,
            "issue_number": issue.issue_number,
            "title": issue.title,
            "priority": self.overview_helpers.option_summary(priority_value),
            "status": self.overview_helpers.option_summary(status_value),
            "target_resolution_date": issue.target_resolution_date,
            "created_at": issue.created_at,
            "project": {"id": project.id, "name": project.name},
            "program": {"id": program.id, "name": program.name},
        }

    def overdue_task_summary(self, row: DashboardTaskRow) -> dict[str, object]:
        task, project, program, status_value, _task_type_value = row
        return {
            "id": task.id,
            "project_id": project.id,
            "program_id": program.id,
            "name": task.name,
            "status": self.overview_helpers.option_summary(status_value),
            "start_date": task.start_date,
            "finish_date": task.finish_date,
            "project": {"id": project.id, "name": project.name},
            "program": {"id": program.id, "name": program.name},
        }

    def resource_utilization_summary(self, row: DashboardResourceUtilizationRow) -> dict[str, object]:
        resource_id, user_id, name, role, weekly_capacity_hours, allocated_hours, project_count = row
        capacity = Decimal(str(weekly_capacity_hours or 0))
        allocated = Decimal(str(allocated_hours or 0))
        utilization_percent = round(float((allocated / capacity) * Decimal("100")), 2) if capacity > 0 else 0.0
        return {
            "resource": {
                "id": resource_id,
                "user_id": user_id,
                "name": name,
                "role": role,
                "weekly_capacity_hours": capacity,
            },
            "allocated_hours": allocated,
            "utilization_percent": utilization_percent,
            "overallocated": capacity > 0 and allocated > capacity,
            "project_count": int(project_count or 0),
        }

    def activity_summary(self, activity: ActivityLog) -> dict[str, object]:
        return {
            "id": activity.id,
            "entity_type": activity.entity_type,
            "entity_id": activity.entity_id,
            "action": activity.action,
            "created_by": activity.created_by,
            "created_at": activity.created_at,
        }

    def status_for_project(self, project: Project, statuses: dict[UUID, OptionValue]) -> OptionValue | None:
        if project.status_id is None:
            return None
        return statuses.get(project.status_id)

    def is_active_project_status(self, option_value: OptionValue | None) -> bool:
        return self.overview_helpers.has_any_option_key(option_value, PROJECT_ACTIVE_KEYS)

    def is_completed_project_status(self, option_value: OptionValue | None) -> bool:
        return self.overview_helpers.has_any_option_key(option_value, PROJECT_COMPLETED_KEYS)

    def is_at_risk_project_status(self, option_value: OptionValue | None) -> bool:
        return self.overview_helpers.has_any_option_key(option_value, PROJECT_AT_RISK_KEYS)

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
        if membership is None or membership.role not in DASHBOARD_READ_ROLES:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")