from datetime import date, timedelta
from decimal import Decimal
import re
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog
from app.models.assumption import Assumption
from app.models.decision import Decision
from app.models.issue import Issue
from app.models.option_value import OptionValue
from app.models.project import Project
from app.models.risk import Risk
from app.models.task import Task
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.project_overview import ProjectOverviewRepository


TASK_COMPLETED_KEYS = {"complete", "completed", "done"}
TASK_IN_PROGRESS_KEYS = {"inprogress", "doing", "active"}
MILESTONE_KEYS = {"milestone"}
OPEN_EXCLUDED_KEYS = {"closed", "resolved", "complete", "completed", "done", "cancelled", "canceled"}
DECISION_COMPLETED_KEYS = {"approved", "closed"}
HIGH_PRIORITY_KEYS = {"high", "critical"}
PRIORITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


class ProjectOverviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.overview = ProjectOverviewRepository(db)

    def get_project_overview(self, *, project_id: UUID, current_user: User) -> dict[str, object]:
        project = self.get_project_or_404(project_id)
        self.require_account_member(account_id=project.account_id, user_id=current_user.id)

        today = self.overview.today()
        task_rows = self.overview.list_project_tasks(project.id)
        risk_rows = self.overview.list_project_risks(project.id)
        issue_rows = self.overview.list_project_issues(project.id)
        assumption_rows = self.overview.list_project_assumptions(project.id)
        decision_rows = self.overview.list_project_decisions(project.id)
        resource_summary = self.build_resource_summary(project.id)
        recent_activity = self.overview.list_recent_activity(account_id=project.account_id, project_id=project.id, limit=10)
        project_status = self.overview.get_option_value(project.status_id)

        task_stats = self.build_task_stats(task_rows=task_rows, today=today)
        open_risks = [row for row in risk_rows if self.is_open_status(row[1])]
        open_issues = [row for row in issue_rows if self.is_open_status(row[1])]
        open_assumptions = [row for row in assumption_rows if self.is_open_status(row[1])]
        pending_decisions = [row for row in decision_rows if self.is_pending_decision_status(row[1])]
        high_priority_open_issues = [row for row in open_issues if self.is_high_priority(row[2])]

        stats = {
            **task_stats,
            "open_risks": len(open_risks),
            "open_issues": len(open_issues),
            "pending_decisions": len(pending_decisions),
            "open_assumptions": len(open_assumptions),
            "resource_count": resource_summary["total_resources"],
            "overallocated_resources": resource_summary["overallocated_resources"],
        }
        health = self.build_health(
            stats=stats,
            has_task_dates=task_stats["has_task_dates"],
            high_priority_open_issues=len(high_priority_open_issues),
        )

        return {
            "project": self.project_summary(project, project_status),
            "stats": {key: value for key, value in stats.items() if key != "has_task_dates"},
            "health": health,
            "upcoming_milestones": [
                self.task_summary(task, task_status, task_type)
                for task, task_status, task_type in self.upcoming_milestones(task_rows=task_rows, today=today)
            ],
            "top_risks": [self.risk_summary(risk, status_value, priority_value) for risk, status_value, priority_value in self.top_risks(open_risks)],
            "top_issues": [
                self.issue_summary(issue, status_value, priority_value)
                for issue, status_value, priority_value in self.top_issues(open_issues)
            ],
            "pending_decisions": [
                self.decision_summary(decision, status_value)
                for decision, status_value in self.top_pending_decisions(pending_decisions)
            ],
            "recent_activity": [self.activity_summary(activity) for activity in recent_activity],
            "resource_summary": resource_summary,
        }

    def build_task_stats(self, *, task_rows: list[tuple[Task, OptionValue | None, OptionValue | None]], today: date) -> dict[str, int | bool]:
        total_tasks = len(task_rows)
        completed_tasks = 0
        in_progress_tasks = 0
        overdue_tasks = 0
        upcoming_milestones = 0
        has_task_dates = False
        milestone_window_end = today + timedelta(days=30)

        for task, status_value, task_type_value in task_rows:
            is_completed = self.is_completed_task_status(status_value)
            has_task_dates = has_task_dates or task.start_date is not None or task.finish_date is not None
            if is_completed:
                completed_tasks += 1
            if self.is_in_progress_task_status(status_value):
                in_progress_tasks += 1
            if task.finish_date is not None and task.finish_date < today and not is_completed:
                overdue_tasks += 1
            if (
                self.is_milestone_type(task_type_value)
                and task.finish_date is not None
                and today <= task.finish_date <= milestone_window_end
            ):
                upcoming_milestones += 1

        return {
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "in_progress_tasks": in_progress_tasks,
            "overdue_tasks": overdue_tasks,
            "upcoming_milestones": upcoming_milestones,
            "has_task_dates": has_task_dates,
        }

    def build_resource_summary(self, project_id: UUID) -> dict[str, int | float]:
        rows = self.overview.list_resource_allocation_rollups(project_id)
        total_allocated_hours = Decimal("0")
        overallocated_resources = 0
        for _resource_id, weekly_capacity_hours, allocated_hours in rows:
            allocated = Decimal(str(allocated_hours or 0))
            capacity = Decimal(str(weekly_capacity_hours or 0))
            total_allocated_hours += allocated
            if capacity > 0 and allocated > capacity:
                overallocated_resources += 1
        return {
            "total_resources": len(rows),
            "total_allocated_hours": float(total_allocated_hours),
            "overallocated_resources": overallocated_resources,
        }

    def build_health(
        self,
        *,
        stats: dict[str, int | bool],
        has_task_dates: bool,
        high_priority_open_issues: int,
    ) -> dict[str, str]:
        total_tasks = int(stats["total_tasks"])
        completed_tasks = int(stats["completed_tasks"])
        overdue_tasks = int(stats["overdue_tasks"])
        upcoming_milestones = int(stats["upcoming_milestones"])
        open_issues = int(stats["open_issues"])
        open_risks = int(stats["open_risks"])
        resource_count = int(stats["resource_count"])
        overallocated_resources = int(stats["overallocated_resources"])

        if total_tasks == 0 or not has_task_dates:
            schedule = "UNKNOWN"
        elif overdue_tasks > 0:
            schedule = "RED"
        elif upcoming_milestones > 0 and completed_tasks < total_tasks:
            schedule = "YELLOW"
        else:
            schedule = "GREEN"

        if high_priority_open_issues > 0:
            scope = "RED"
        elif open_issues > 0 or open_risks > 0:
            scope = "YELLOW"
        else:
            scope = "GREEN"

        if overallocated_resources > 0:
            resources = "RED"
        elif resource_count > 0:
            resources = "GREEN"
        else:
            resources = "UNKNOWN"

        dimensions = [schedule, scope, resources]
        if "RED" in dimensions:
            overall = "RED"
        elif "YELLOW" in dimensions:
            overall = "YELLOW"
        elif all(dimension == "UNKNOWN" for dimension in dimensions):
            overall = "UNKNOWN"
        else:
            overall = "GREEN"
        return {"schedule": schedule, "scope": scope, "resources": resources, "overall": overall}

    def upcoming_milestones(
        self,
        *,
        task_rows: list[tuple[Task, OptionValue | None, OptionValue | None]],
        today: date,
    ) -> list[tuple[Task, OptionValue | None, OptionValue | None]]:
        milestone_window_end = today + timedelta(days=30)
        milestones = [
            row
            for row in task_rows
            if self.is_milestone_type(row[2])
            and row[0].finish_date is not None
            and today <= row[0].finish_date <= milestone_window_end
        ]
        return sorted(milestones, key=lambda row: (row[0].finish_date or date.max, row[0].sort_order, row[0].name, row[0].id))[:5]

    def top_risks(self, risk_rows: list[tuple[Risk, OptionValue | None, OptionValue | None]]) -> list[tuple[Risk, OptionValue | None, OptionValue | None]]:
        return sorted(
            risk_rows,
            key=lambda row: (self.priority_rank(row[2]), row[0].target_resolution_date or date.max, row[0].created_at, row[0].id),
        )[:5]

    def top_issues(self, issue_rows: list[tuple[Issue, OptionValue | None, OptionValue | None]]) -> list[tuple[Issue, OptionValue | None, OptionValue | None]]:
        return sorted(
            issue_rows,
            key=lambda row: (self.priority_rank(row[2]), row[0].target_resolution_date or date.max, row[0].created_at, row[0].id),
        )[:5]

    def top_pending_decisions(
        self,
        decision_rows: list[tuple[Decision, OptionValue | None]],
    ) -> list[tuple[Decision, OptionValue | None]]:
        return sorted(decision_rows, key=lambda row: (row[0].proposed_date or date.max, row[0].created_at, row[0].id))[:5]

    def project_summary(self, project: Project, status_value: OptionValue | None) -> dict[str, object]:
        return {
            "id": project.id,
            "account_id": project.account_id,
            "portfolio_id": project.portfolio_id,
            "program_id": project.program_id,
            "name": project.name,
            "description": project.description,
            "status": self.option_summary(status_value),
            "start_date": project.start_date,
            "target_end_date": project.target_end_date,
        }

    def task_summary(self, task: Task, status_value: OptionValue | None, task_type_value: OptionValue | None) -> dict[str, object]:
        return {
            "id": task.id,
            "name": task.name,
            "status": self.option_summary(status_value),
            "task_type": self.option_summary(task_type_value),
            "start_date": task.start_date,
            "finish_date": task.finish_date,
        }

    def risk_summary(self, risk: Risk, status_value: OptionValue | None, priority_value: OptionValue | None) -> dict[str, object]:
        return {
            "id": risk.id,
            "risk_number": risk.risk_number,
            "title": risk.title,
            "priority": self.option_summary(priority_value),
            "status": self.option_summary(status_value),
            "target_resolution_date": risk.target_resolution_date,
            "created_at": risk.created_at,
        }

    def issue_summary(self, issue: Issue, status_value: OptionValue | None, priority_value: OptionValue | None) -> dict[str, object]:
        return {
            "id": issue.id,
            "issue_number": issue.issue_number,
            "title": issue.title,
            "priority": self.option_summary(priority_value),
            "status": self.option_summary(status_value),
            "target_resolution_date": issue.target_resolution_date,
            "created_at": issue.created_at,
        }

    def decision_summary(self, decision: Decision, status_value: OptionValue | None) -> dict[str, object]:
        return {
            "id": decision.id,
            "decision_number": decision.decision_number,
            "title": decision.title,
            "status": self.option_summary(status_value),
            "proposed_date": decision.proposed_date,
            "approved_date": decision.approved_date,
            "created_at": decision.created_at,
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

    def option_summary(self, option_value: OptionValue | None) -> dict[str, object] | None:
        if option_value is None:
            return None
        return {
            "id": option_value.id,
            "label": option_value.label,
            "value": option_value.value,
            "color": option_value.color,
        }

    def is_completed_task_status(self, option_value: OptionValue | None) -> bool:
        return self.has_any_option_key(option_value, TASK_COMPLETED_KEYS)

    def is_in_progress_task_status(self, option_value: OptionValue | None) -> bool:
        return self.has_any_option_key(option_value, TASK_IN_PROGRESS_KEYS)

    def is_milestone_type(self, option_value: OptionValue | None) -> bool:
        return self.has_any_option_key(option_value, MILESTONE_KEYS)

    def is_open_status(self, option_value: OptionValue | None) -> bool:
        return not self.has_any_option_key(option_value, OPEN_EXCLUDED_KEYS)

    def is_pending_decision_status(self, option_value: OptionValue | None) -> bool:
        return not self.has_any_option_key(option_value, DECISION_COMPLETED_KEYS)

    def is_high_priority(self, option_value: OptionValue | None) -> bool:
        return self.has_any_option_key(option_value, HIGH_PRIORITY_KEYS)

    def priority_rank(self, option_value: OptionValue | None) -> int:
        keys = self.option_keys(option_value)
        for key, rank in PRIORITY_RANK.items():
            if key in keys:
                return rank
        return len(PRIORITY_RANK)

    def has_any_option_key(self, option_value: OptionValue | None, keys: set[str]) -> bool:
        return bool(self.option_keys(option_value) & keys)

    def option_keys(self, option_value: OptionValue | None) -> set[str]:
        if option_value is None:
            return set()
        return {
            self.normalize_option_key(option_value.value),
            self.normalize_option_key(option_value.label),
        }

    def normalize_option_key(self, value: str | None) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())

    def get_project_or_404(self, project_id: UUID) -> Project:
        project = self.overview.get_project(project_id)
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
