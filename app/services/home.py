"""The signed-in person's home page: their dated work, the projects they're on, recent activity there,
and the approvals, assignments and mentions waiting on them."""

from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.access_grant import ProjectModule
from app.models.assumption import Assumption
from app.models.change_request import ChangeRequest
from app.models.comment import Comment
from app.models.comment_mention import CommentMention
from app.models.decision import Decision
from app.models.dependency import Dependency
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.project import Project
from app.models.resource import Resource
from app.models.resource_allocation import ResourceAllocation
from app.models.task_assignment import TaskAssignment
from app.models.user import User
from app.repositories.account_dashboard import AccountDashboardRepository, DashboardTaskRow
from app.repositories.hierarchy import HierarchyRepository
from app.services.access import access_for
from app.services.account_dashboard import AccountDashboardService
from app.services.activity_display import describe_activity
from app.services.project_overview import ProjectOverviewService


WEEK_DAYS = 7
PROJECT_LIMIT = 8
ACTIVITY_LIMIT = 10
NOTIFICATION_LIMIT = 8
# Which status option marks an item done, in order of preference (matched on the option's value or label).
COMPLETE_STATUS_KEYS = {
    "TASK": ("complete", "completed", "done"),
    "RISK": ("closed", "resolved"),
    "ISSUE": ("closed", "resolved"),
    "ASSUMPTION": ("closed", "validated"),
    "DEPENDENCY": ("closed",),
}
# Change requests and decisions in these states are waiting for someone to approve them.
AWAITING_APPROVAL_KEYS = {"open", "inreview", "pending", "pendingapproval", "submitted"}

# A row of (record, project, status option) for logs the dashboard repository doesn't cover.
LogRow = tuple[Any, Project, OptionValue | None]


class HomeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.hierarchy = HierarchyRepository(db)
        self.dashboard = AccountDashboardRepository(db)
        self.dashboard_service = AccountDashboardService(db)
        self.helpers = ProjectOverviewService(db)

    def get_home(self, *, account_id: UUID, current_user: User) -> dict[str, object]:
        self.dashboard_service.require_account_member(account_id=account_id, user_id=current_user.id)
        access = access_for(self.db, account_id=account_id, user=current_user)
        projects = self.hierarchy.list_projects_for_account(account_id)
        if not access.is_owner:
            projects = access.keep_visible(projects, access.visible_project_ids())
        project_ids = [project.id for project in projects]
        today = self.dashboard.today()
        is_me = self.name_matcher(current_user)

        def where_visible(module: ProjectModule) -> list[UUID]:
            visible = access.visible_project_ids(module)
            return project_ids if visible is None else [pid for pid in project_ids if pid in visible]

        task_rows = self.dashboard.list_tasks_for_projects(where_visible(ProjectModule.TASKS))
        risk_rows = self.dashboard.list_risks_for_projects(where_visible(ProjectModule.RISKS))
        issue_rows = self.dashboard.list_issues_for_projects(where_visible(ProjectModule.ISSUES))
        assumption_rows = self.list_log_rows(Assumption, where_visible(ProjectModule.ASSUMPTIONS))
        dependency_rows = self.list_log_rows(Dependency, where_visible(ProjectModule.DEPENDENCIES))
        decision_rows = self.list_log_rows(Decision, where_visible(ProjectModule.DECISIONS))
        change_request_rows = self.list_log_rows(ChangeRequest, where_visible(ProjectModule.CHANGE_REQUESTS))

        my_task_ids = self.task_ids_assigned_to(current_user.id, [row[0].id for row in task_rows])
        my_tasks = [row for row in task_rows if row[0].id in my_task_ids]
        # Risks and issues can be assigned to a member or just by name; assumptions and dependencies only by name.
        my_risks = [row for row in risk_rows if row[0].assigned_to == current_user.id or is_me(row[0].assigned_to_name)]
        my_issues = [row for row in issue_rows if row[0].assigned_to == current_user.id or is_me(row[0].assigned_to_name)]
        my_assumptions = [row for row in assumption_rows if is_me(row[0].assigned_to_name)]
        my_dependencies = [row for row in dependency_rows if is_me(row[0].assigned_to_name)]

        complete_status_ids = self.complete_status_ids(account_id)
        priorities: list[dict[str, object]] = []
        for task, project, _program, status_value, _type in my_tasks:
            if task.finish_date is None or task.percent_complete >= 100 or self.helpers.is_completed_task_status(status_value):
                continue
            priorities.append(self.priority_item(task.id, "TASK", task.name, project, task.finish_date, today, complete_status_ids))
        for kind, rows in (("RISK", my_risks), ("ISSUE", my_issues)):
            for item, project, _program, status_value, _priority in rows:
                if item.target_resolution_date is None or not self.helpers.is_open_status(status_value):
                    continue
                priorities.append(
                    self.priority_item(item.id, kind, item.title, project, item.target_resolution_date, today, complete_status_ids)
                )
        for kind, rows in (("ASSUMPTION", my_assumptions), ("DEPENDENCY", my_dependencies)):
            for item, project, status_value in rows:
                if item.target_date is None or not self.helpers.is_open_status(status_value):
                    continue
                priorities.append(
                    self.priority_item(item.id, kind, self.title_of(item), project, item.target_date, today, complete_status_ids)
                )
        priorities.sort(key=lambda item: (item["due_date"], str(item["title"]).lower(), str(item["id"])))

        # "My projects": ones I manage (or created, while no manager is set) and ones where I have tasks.
        mine = {row[1].id for row in my_tasks}
        mine |= {
            project.id
            for project in projects
            if is_me(project.project_manager) or (not project.project_manager and project.created_by == current_user.id)
        }
        my_projects = [project for project in projects if project.id in mine]
        my_projects.sort(key=lambda project: project.updated_at, reverse=True)

        project_statuses = self.hierarchy.get_status_values_by_ids(
            {project.status_id for project in my_projects if project.status_id is not None}
        )
        my_project_ids = [project.id for project in my_projects]
        at_risk_ids = {
            item["id"]
            for item in self.dashboard_service.projects_at_risk(
                projects=my_projects,
                project_statuses=project_statuses,
                task_rows=[row for row in task_rows if row[1].id in mine],
                risk_rows=[row for row in risk_rows if row[1].id in mine],
                issue_rows=[row for row in issue_rows if row[1].id in mine],
                resource_rows=self.dashboard.list_project_resource_rollups(my_project_ids),
                today=today,
            )
        }
        tasks_by_project: dict[UUID, list[DashboardTaskRow]] = defaultdict(list)
        for row in task_rows:
            tasks_by_project[row[1].id].append(row)

        project_summaries = []
        active_project_count = 0
        for project in my_projects:
            status_value = self.dashboard_service.status_for_project(project, project_statuses)
            is_completed = self.dashboard_service.is_completed_project_status(status_value)
            if self.dashboard_service.is_active_project_status(status_value):
                active_project_count += 1
            project_summaries.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "status": self.helpers.option_summary(status_value),
                    "is_completed": is_completed,
                    "at_risk": not is_completed and project.id in at_risk_ids,
                    "percent_complete": 100 if is_completed else self.percent_complete(tasks_by_project[project.id], today),
                    "next_milestone": self.next_milestone(tasks_by_project[project.id], today),
                    "target_end_date": project.target_end_date,
                }
            )

        # Every visible record by id, with its project and a display name, for activity and notifications.
        records: dict[UUID, tuple[str, Project]] = {project.id: (project.name, project) for project in projects}
        for rows, name_of in (
            (task_rows, lambda item: item.name),
            (risk_rows, lambda item: item.title),
            (issue_rows, lambda item: item.title),
            (assumption_rows, self.title_of),
            (dependency_rows, self.title_of),
            (decision_rows, self.title_of),
            (change_request_rows, self.title_of),
        ):
            for row in rows:
                records[row[0].id] = (name_of(row[0]), row[1])

        notifications = self.notifications(
            account_id=account_id,
            current_user=current_user,
            is_me=is_me,
            my_tasks=my_tasks,
            decision_rows=decision_rows,
            change_request_rows=change_request_rows,
            records=records,
        )

        return {
            "priorities": priorities,
            "counts": {
                "all": len(priorities),
                "overdue": sum(1 for item in priorities if item["bucket"] == "OVERDUE"),
                "week": sum(1 for item in priorities if item["bucket"] == "WEEK"),
                "upcoming": sum(1 for item in priorities if item["bucket"] == "UPCOMING"),
            },
            "active_project_count": active_project_count,
            "my_projects": project_summaries[:PROJECT_LIMIT],
            "recent_activity": self.recent_activity(
                account_id=account_id,
                access=access,
                projects=my_projects,
                records=records,
            ),
            "notifications": notifications,
            "notifications_read_at": current_user.home_notifications_read_at,
        }

    def mark_notifications_read(self, *, account_id: UUID, current_user: User) -> dict[str, object]:
        self.dashboard_service.require_account_member(account_id=account_id, user_id=current_user.id)
        current_user.home_notifications_read_at = datetime.now(UTC)
        self.db.commit()
        return {"notifications_read_at": current_user.home_notifications_read_at}

    def notifications(
        self,
        *,
        account_id: UUID,
        current_user: User,
        is_me,
        my_tasks: list[DashboardTaskRow],
        decision_rows: list[LogRow],
        change_request_rows: list[LogRow],
        records: dict[UUID, tuple[str, Project]],
    ) -> list[dict[str, object]]:
        """Approvals waiting on the person, open work assigned to them, and comments that mention them."""
        items: list[dict[str, object]] = []

        for entity_type, label, rows in (
            ("CHANGE_REQUEST", "Change Request", change_request_rows),
            ("DECISION", "Decision", decision_rows),
        ):
            for item, project, status_value in rows:
                awaiting = bool(self.helpers.option_keys(status_value) & AWAITING_APPROVAL_KEYS)
                if not awaiting or is_me(item.proposed_by_name) or item.created_by == current_user.id:
                    continue
                number = getattr(item, f"{entity_type.lower()}_number", "")
                items.append(
                    self.notification(
                        f"approval-{item.id}", "APPROVAL", "Approval requested",
                        f"{project.name} — {label} {number}".strip(), entity_type, item.id, project, item.updated_at,
                    )
                )

        for task, project, _program, status_value, _type in my_tasks:
            if task.percent_complete >= 100 or self.helpers.is_completed_task_status(status_value):
                continue
            items.append(
                self.notification(
                    f"assignment-{task.id}", "ASSIGNMENT", "New task assigned to you",
                    f"{project.name} — {task.name}", "TASK", task.id, project, task.updated_at,
                )
            )

        mentions = self.db.execute(
            select(Comment)
            .join(CommentMention, CommentMention.comment_id == Comment.id)
            .where(CommentMention.mentioned_user_id == current_user.id, Comment.account_id == account_id)
            .order_by(Comment.created_at.desc())
            .limit(NOTIFICATION_LIMIT * 4)
        ).scalars().all()
        for comment in mentions:
            # Only comments on records the person can see.
            record = records.get(comment.entity_id)
            if record is None:
                continue
            name, project = record
            items.append(
                self.notification(
                    f"mention-{comment.id}", "MENTION", "You were mentioned in a comment",
                    f"{project.name} — {name}", comment.entity_type, comment.entity_id, project, comment.created_at,
                )
            )

        items.sort(key=lambda item: item["created_at"], reverse=True)
        return items[:NOTIFICATION_LIMIT]

    def notification(
        self, item_id: str, kind: str, title: str, subtitle: str, entity_type: str, entity_id: UUID, project: Project, created_at: datetime,
    ) -> dict[str, object]:
        return {
            "id": item_id,
            "kind": kind,
            "title": title,
            "subtitle": subtitle,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "project": {"id": project.id, "name": project.name},
            "created_at": created_at,
        }

    def name_matcher(self, user: User):
        """Whether a free-text name (an assignee, a project manager) refers to this person."""
        names = {
            " ".join(name.split()).lower()
            for name in (user.full_name, f"{user.first_name or ''} {user.last_name or ''}")
            if name and name.strip()
        }

        def is_me(value: str | None) -> bool:
            return bool(value) and " ".join(str(value).split()).lower() in names

        return is_me

    def title_of(self, item: Any) -> str:
        return getattr(item, "title", None) or getattr(item, "description", None) or ""

    def list_log_rows(self, model: Any, project_ids: list[UUID]) -> list[LogRow]:
        if not project_ids:
            return []
        statement = (
            select(model, Project, OptionValue)
            .join(Project, Project.id == model.project_id)
            .outerjoin(OptionValue, OptionValue.id == model.status_id)
            .where(model.project_id.in_(project_ids))
        )
        return list(self.db.execute(statement).all())

    def priority_item(
        self,
        item_id: UUID,
        kind: str,
        title: str,
        project: Project,
        due_date: date,
        today: date,
        complete_status_ids: dict[str, UUID],
    ) -> dict[str, object]:
        days = (due_date - today).days
        bucket = "OVERDUE" if days < 0 else "WEEK" if days <= WEEK_DAYS else "UPCOMING"
        return {
            "id": item_id,
            "kind": kind,
            "title": title,
            "project": {"id": project.id, "name": project.name},
            "due_date": due_date,
            "bucket": bucket,
            "complete_status_id": complete_status_ids.get(kind),
        }

    def percent_complete(self, rows: list[DashboardTaskRow], today: date) -> int:
        stats = self.dashboard_service.build_task_stats(task_rows=rows, today=today)
        total = int(stats["total_tasks"])
        return round(int(stats["completed_tasks"]) * 100 / total) if total else 0

    def next_milestone(self, rows: list[DashboardTaskRow], today: date) -> dict[str, object] | None:
        upcoming = [
            task
            for task, _project, _program, status_value, type_value in rows
            if self.helpers.is_milestone_type(type_value)
            and task.finish_date is not None
            and task.finish_date >= today
            and not self.helpers.is_completed_task_status(status_value)
        ]
        if not upcoming:
            return None
        task = min(upcoming, key=lambda item: (item.finish_date, item.sort_order, item.id))
        return {"id": task.id, "name": task.name, "finish_date": task.finish_date}

    def task_ids_assigned_to(self, user_id: UUID, task_ids: list[UUID]) -> set[UUID]:
        """Tasks the person is assigned to directly, or through a resource linked to them."""
        if not task_ids:
            return set()
        assigned = select(TaskAssignment.task_id).where(TaskAssignment.user_id == user_id, TaskAssignment.task_id.in_(task_ids))
        allocated = (
            select(ResourceAllocation.task_id)
            .join(Resource, Resource.id == ResourceAllocation.resource_id)
            .where(Resource.user_id == user_id, ResourceAllocation.task_id.in_(task_ids))
        )
        return set(self.db.scalars(assigned.union(allocated)).all())

    def complete_status_ids(self, account_id: UUID) -> dict[str, UUID]:
        rows = self.db.execute(
            select(OptionSet.entity_type, OptionValue)
            .join(OptionValue, OptionValue.option_set_id == OptionSet.id)
            .where(
                OptionSet.account_id == account_id,
                OptionSet.name == "STATUS",
                OptionSet.entity_type.in_(COMPLETE_STATUS_KEYS),
                OptionValue.is_active.is_(True),
            )
            .order_by(OptionValue.sort_order, OptionValue.id)
        ).all()
        found: dict[str, UUID] = {}
        for entity_type, keys in COMPLETE_STATUS_KEYS.items():
            options = [option for row_type, option in rows if row_type == entity_type]
            for key in keys:
                match = next((option for option in options if key in self.helpers.option_keys(option)), None)
                if match is not None:
                    found[entity_type] = match.id
                    break
        return found

    def recent_activity(
        self,
        *,
        account_id: UUID,
        access,
        projects: list[Project],
        records: dict[UUID, tuple[str, Project]],
    ) -> list[dict[str, object]]:
        if not projects:
            return []
        entries = self.dashboard.list_recent_activity(
            account_id=account_id,
            portfolio_ids=[],
            program_ids=[],
            project_ids=[project.id for project in projects],
            restrict_to_scope=True,
            limit=ACTIVITY_LIMIT * 4,
        )
        entries = [entry for entry in entries if access.can_view_activity(entry.entity_type, entry.entity_id)][:ACTIVITY_LIMIT]

        described = []
        for entry in describe_activity(self.db, account_id, entries):
            project = records.get(entry["entity_id"], (None, None))[1]
            described.append(
                {
                    "id": entry["id"],
                    "entity_type": entry["entity_type"],
                    "entity_id": entry["entity_id"],
                    "action": entry["action"],
                    "entity_name": entry["entity_name"],
                    "actor_name": entry["actor_name"],
                    "project": {"id": project.id, "name": project.name} if project else None,
                    "created_at": entry["created_at"],
                }
            )
        return described
