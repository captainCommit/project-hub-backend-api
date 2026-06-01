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
from app.models.project import Project
from app.models.resource import Resource
from app.models.resource_allocation import ResourceAllocation
from app.models.risk import Risk
from app.models.task import Task


TaskOverviewRow = tuple[Task, OptionValue | None, OptionValue | None]
RiskOverviewRow = tuple[Risk, OptionValue | None, OptionValue | None]
IssueOverviewRow = tuple[Issue, OptionValue | None, OptionValue | None]
AssumptionOverviewRow = tuple[Assumption, OptionValue | None]
DecisionOverviewRow = tuple[Decision, OptionValue | None]
ResourceOverviewRow = tuple[UUID, Decimal, Decimal]


class ProjectOverviewRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_project(self, project_id: UUID) -> Project | None:
        return self.db.get(Project, project_id)

    def get_option_value(self, option_value_id: UUID | None) -> OptionValue | None:
        if option_value_id is None:
            return None
        return self.db.get(OptionValue, option_value_id)

    def list_project_tasks(self, project_id: UUID) -> list[TaskOverviewRow]:
        task_status = aliased(OptionValue)
        task_type = aliased(OptionValue)
        statement = (
            select(Task, task_status, task_type)
            .outerjoin(task_status, task_status.id == Task.status_id)
            .outerjoin(task_type, task_type.id == Task.task_type_id)
            .where(Task.project_id == project_id, Task.is_deleted.is_(False))
            .order_by(Task.sort_order, Task.finish_date, Task.name, Task.id)
        )
        return list(self.db.execute(statement).all())

    def list_project_risks(self, project_id: UUID) -> list[RiskOverviewRow]:
        risk_status = aliased(OptionValue)
        risk_priority = aliased(OptionValue)
        statement = (
            select(Risk, risk_status, risk_priority)
            .outerjoin(risk_status, risk_status.id == Risk.status_id)
            .outerjoin(risk_priority, risk_priority.id == Risk.priority_id)
            .where(Risk.project_id == project_id)
            .order_by(Risk.created_at, Risk.id)
        )
        return list(self.db.execute(statement).all())

    def list_project_issues(self, project_id: UUID) -> list[IssueOverviewRow]:
        issue_status = aliased(OptionValue)
        issue_priority = aliased(OptionValue)
        statement = (
            select(Issue, issue_status, issue_priority)
            .outerjoin(issue_status, issue_status.id == Issue.status_id)
            .outerjoin(issue_priority, issue_priority.id == Issue.priority_id)
            .where(Issue.project_id == project_id)
            .order_by(Issue.created_at, Issue.id)
        )
        return list(self.db.execute(statement).all())

    def list_project_assumptions(self, project_id: UUID) -> list[AssumptionOverviewRow]:
        assumption_status = aliased(OptionValue)
        statement = (
            select(Assumption, assumption_status)
            .outerjoin(assumption_status, assumption_status.id == Assumption.status_id)
            .where(Assumption.project_id == project_id)
            .order_by(Assumption.created_at, Assumption.id)
        )
        return list(self.db.execute(statement).all())

    def list_project_decisions(self, project_id: UUID) -> list[DecisionOverviewRow]:
        decision_status = aliased(OptionValue)
        statement = (
            select(Decision, decision_status)
            .outerjoin(decision_status, decision_status.id == Decision.status_id)
            .where(Decision.project_id == project_id)
            .order_by(Decision.created_at, Decision.id)
        )
        return list(self.db.execute(statement).all())

    def list_resource_allocation_rollups(self, project_id: UUID) -> list[ResourceOverviewRow]:
        total_allocated_hours = func.coalesce(func.sum(ResourceAllocation.allocated_hours), 0)
        statement = (
            select(
                Resource.id,
                Resource.weekly_capacity_hours,
                total_allocated_hours.label("total_allocated_hours"),
            )
            .join(ResourceAllocation, ResourceAllocation.resource_id == Resource.id)
            .join(Task, Task.id == ResourceAllocation.task_id)
            .where(
                Task.project_id == project_id,
                Task.is_deleted.is_(False),
                Resource.is_active.is_(True),
            )
            .group_by(Resource.id, Resource.weekly_capacity_hours)
            .order_by(Resource.id)
        )
        return list(self.db.execute(statement).all())

    def list_recent_activity(self, *, account_id: UUID, project_id: UUID, limit: int = 10) -> list[ActivityLog]:
        project_task_ids = select(Task.id).where(Task.project_id == project_id)
        project_decision_ids = select(Decision.id).where(Decision.project_id == project_id)
        project_decision_option_ids = select(DecisionOption.id).where(
            DecisionOption.decision_id.in_(project_decision_ids)
        )
        project_resource_allocation_ids = (
            select(ResourceAllocation.id)
            .join(Task, Task.id == ResourceAllocation.task_id)
            .where(Task.project_id == project_id, Task.is_deleted.is_(False))
        )
        conditions = [
            and_(ActivityLog.entity_type == "PROJECT", ActivityLog.entity_id == project_id),
            and_(ActivityLog.entity_type == "TASK", ActivityLog.entity_id.in_(project_task_ids)),
            and_(
                ActivityLog.entity_type == "RISK",
                ActivityLog.entity_id.in_(select(Risk.id).where(Risk.project_id == project_id)),
            ),
            and_(
                ActivityLog.entity_type == "ISSUE",
                ActivityLog.entity_id.in_(select(Issue.id).where(Issue.project_id == project_id)),
            ),
            and_(
                ActivityLog.entity_type == "ASSUMPTION",
                ActivityLog.entity_id.in_(select(Assumption.id).where(Assumption.project_id == project_id)),
            ),
            and_(ActivityLog.entity_type == "DECISION", ActivityLog.entity_id.in_(project_decision_ids)),
            and_(ActivityLog.entity_type == "DECISION_OPTION", ActivityLog.entity_id.in_(project_decision_option_ids)),
            and_(
                ActivityLog.entity_type == "RESOURCE_ALLOCATION",
                ActivityLog.entity_id.in_(project_resource_allocation_ids),
            ),
        ]
        statement = (
            select(ActivityLog)
            .where(ActivityLog.account_id == account_id, or_(*conditions))
            .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
            .limit(limit)
        )
        return list(self.db.scalars(statement).all())

    def today(self) -> date:
        return date.today()