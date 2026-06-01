from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.activity_log import ActivityLog
from app.models.assumption import Assumption
from app.models.decision import Decision
from app.models.decision_option import DecisionOption
from app.models.issue import Issue
from app.models.option_value import OptionValue
from app.models.program import Program
from app.models.project import Project
from app.models.resource import Resource
from app.models.resource_allocation import ResourceAllocation
from app.models.risk import Risk
from app.models.task import Task


DashboardTaskRow = tuple[Task, Project, Program, OptionValue | None, OptionValue | None]
DashboardRiskRow = tuple[Risk, Project, Program, OptionValue | None, OptionValue | None]
DashboardIssueRow = tuple[Issue, Project, Program, OptionValue | None, OptionValue | None]
DashboardDecisionRow = tuple[Decision, Project, Program, OptionValue | None]
DashboardResourceUtilizationRow = tuple[UUID, UUID | None, str, str | None, Decimal, Decimal, int]
DashboardProjectResourceRollupRow = tuple[UUID, UUID, Decimal, Decimal]


class AccountDashboardRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_tasks_for_projects(self, project_ids: Iterable[UUID]) -> list[DashboardTaskRow]:
        project_ids = list(project_ids)
        if not project_ids:
            return []

        task_status = aliased(OptionValue)
        task_type = aliased(OptionValue)
        statement = (
            select(Task, Project, Program, task_status, task_type)
            .join(Project, Project.id == Task.project_id)
            .join(Program, Program.id == Project.program_id)
            .outerjoin(task_status, task_status.id == Task.status_id)
            .outerjoin(task_type, task_type.id == Task.task_type_id)
            .where(Task.project_id.in_(project_ids), Task.is_deleted.is_(False))
            .order_by(Task.finish_date, Task.sort_order, Task.created_at, Task.id)
        )
        return list(self.db.execute(statement).all())

    def list_risks_for_projects(self, project_ids: Iterable[UUID]) -> list[DashboardRiskRow]:
        project_ids = list(project_ids)
        if not project_ids:
            return []

        risk_status = aliased(OptionValue)
        risk_priority = aliased(OptionValue)
        statement = (
            select(Risk, Project, Program, risk_status, risk_priority)
            .join(Project, Project.id == Risk.project_id)
            .join(Program, Program.id == Project.program_id)
            .outerjoin(risk_status, risk_status.id == Risk.status_id)
            .outerjoin(risk_priority, risk_priority.id == Risk.priority_id)
            .where(Risk.project_id.in_(project_ids))
            .order_by(Risk.created_at, Risk.id)
        )
        return list(self.db.execute(statement).all())

    def list_issues_for_projects(self, project_ids: Iterable[UUID]) -> list[DashboardIssueRow]:
        project_ids = list(project_ids)
        if not project_ids:
            return []

        issue_status = aliased(OptionValue)
        issue_priority = aliased(OptionValue)
        statement = (
            select(Issue, Project, Program, issue_status, issue_priority)
            .join(Project, Project.id == Issue.project_id)
            .join(Program, Program.id == Project.program_id)
            .outerjoin(issue_status, issue_status.id == Issue.status_id)
            .outerjoin(issue_priority, issue_priority.id == Issue.priority_id)
            .where(Issue.project_id.in_(project_ids))
            .order_by(Issue.created_at, Issue.id)
        )
        return list(self.db.execute(statement).all())

    def list_decisions_for_projects(self, project_ids: Iterable[UUID]) -> list[DashboardDecisionRow]:
        project_ids = list(project_ids)
        if not project_ids:
            return []

        decision_status = aliased(OptionValue)
        statement = (
            select(Decision, Project, Program, decision_status)
            .join(Project, Project.id == Decision.project_id)
            .join(Program, Program.id == Project.program_id)
            .outerjoin(decision_status, decision_status.id == Decision.status_id)
            .where(Decision.project_id.in_(project_ids))
            .order_by(Decision.created_at, Decision.id)
        )
        return list(self.db.execute(statement).all())

    def list_resource_utilization_rollups(
        self,
        project_ids: Iterable[UUID],
    ) -> list[DashboardResourceUtilizationRow]:
        project_ids = list(project_ids)
        if not project_ids:
            return []

        total_allocated_hours = func.coalesce(func.sum(ResourceAllocation.allocated_hours), 0)
        project_count = func.count(func.distinct(Task.project_id))
        statement = (
            select(
                Resource.id,
                Resource.user_id,
                Resource.name,
                Resource.role,
                Resource.weekly_capacity_hours,
                total_allocated_hours.label("total_allocated_hours"),
                project_count.label("project_count"),
            )
            .join(ResourceAllocation, ResourceAllocation.resource_id == Resource.id)
            .join(Task, Task.id == ResourceAllocation.task_id)
            .where(
                Task.project_id.in_(project_ids),
                Task.is_deleted.is_(False),
                Resource.is_active.is_(True),
            )
            .group_by(Resource.id, Resource.user_id, Resource.name, Resource.role, Resource.weekly_capacity_hours)
            .order_by(total_allocated_hours.desc(), Resource.name, Resource.id)
        )
        return list(self.db.execute(statement).all())

    def list_project_resource_rollups(
        self,
        project_ids: Iterable[UUID],
    ) -> list[DashboardProjectResourceRollupRow]:
        project_ids = list(project_ids)
        if not project_ids:
            return []

        total_allocated_hours = func.coalesce(func.sum(ResourceAllocation.allocated_hours), 0)
        statement = (
            select(
                Task.project_id,
                Resource.id,
                Resource.weekly_capacity_hours,
                total_allocated_hours.label("total_allocated_hours"),
            )
            .join(ResourceAllocation, ResourceAllocation.resource_id == Resource.id)
            .join(Task, Task.id == ResourceAllocation.task_id)
            .where(
                Task.project_id.in_(project_ids),
                Task.is_deleted.is_(False),
                Resource.is_active.is_(True),
            )
            .group_by(Task.project_id, Resource.id, Resource.weekly_capacity_hours)
            .order_by(Task.project_id, Resource.id)
        )
        return list(self.db.execute(statement).all())

    def list_recent_activity(
        self,
        *,
        account_id: UUID,
        portfolio_ids: Iterable[UUID],
        program_ids: Iterable[UUID],
        project_ids: Iterable[UUID],
        restrict_to_scope: bool,
        limit: int = 10,
    ) -> list[ActivityLog]:
        if not restrict_to_scope:
            statement = (
                select(ActivityLog)
                .where(ActivityLog.account_id == account_id)
                .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
                .limit(limit)
            )
            return list(self.db.scalars(statement).all())

        portfolio_ids = list(portfolio_ids)
        program_ids = list(program_ids)
        project_ids = list(project_ids)
        conditions = []

        if portfolio_ids:
            conditions.append(and_(ActivityLog.entity_type == "PORTFOLIO", ActivityLog.entity_id.in_(portfolio_ids)))
        if program_ids:
            conditions.append(and_(ActivityLog.entity_type == "PROGRAM", ActivityLog.entity_id.in_(program_ids)))
        if project_ids:
            project_task_ids = select(Task.id).where(Task.project_id.in_(project_ids))
            project_decision_ids = select(Decision.id).where(Decision.project_id.in_(project_ids))
            project_decision_option_ids = select(DecisionOption.id).where(
                DecisionOption.decision_id.in_(project_decision_ids)
            )
            project_resource_allocation_ids = (
                select(ResourceAllocation.id)
                .join(Task, Task.id == ResourceAllocation.task_id)
                .where(Task.project_id.in_(project_ids), Task.is_deleted.is_(False))
            )
            conditions.extend(
                [
                    and_(ActivityLog.entity_type == "PROJECT", ActivityLog.entity_id.in_(project_ids)),
                    and_(ActivityLog.entity_type == "TASK", ActivityLog.entity_id.in_(project_task_ids)),
                    and_(
                        ActivityLog.entity_type == "RISK",
                        ActivityLog.entity_id.in_(select(Risk.id).where(Risk.project_id.in_(project_ids))),
                    ),
                    and_(
                        ActivityLog.entity_type == "ISSUE",
                        ActivityLog.entity_id.in_(select(Issue.id).where(Issue.project_id.in_(project_ids))),
                    ),
                    and_(
                        ActivityLog.entity_type == "ASSUMPTION",
                        ActivityLog.entity_id.in_(select(Assumption.id).where(Assumption.project_id.in_(project_ids))),
                    ),
                    and_(ActivityLog.entity_type == "DECISION", ActivityLog.entity_id.in_(project_decision_ids)),
                    and_(ActivityLog.entity_type == "DECISION_OPTION", ActivityLog.entity_id.in_(project_decision_option_ids)),
                    and_(
                        ActivityLog.entity_type == "RESOURCE_ALLOCATION",
                        ActivityLog.entity_id.in_(project_resource_allocation_ids),
                    ),
                ]
            )

        if not conditions:
            return []

        statement = (
            select(ActivityLog)
            .where(ActivityLog.account_id == account_id, or_(*conditions))
            .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
            .limit(limit)
        )
        return list(self.db.scalars(statement).all())

    def today(self) -> date:
        return date.today()