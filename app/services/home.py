"""The signed-in person's home page: their dated work, the projects they're on, recent activity there,
and the approvals, assignments and mentions waiting on them."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, and_, false, func, or_, select
from sqlalchemy.orm import Session

from app.models.access_grant import ProjectModule
from app.models.activity_log import ActivityLog
from app.models.assumption import Assumption
from app.models.change_request import ChangeRequest
from app.models.comment import Comment
from app.models.comment_mention import CommentMention
from app.models.decision import Decision
from app.models.dependency import Dependency
from app.models.issue import Issue
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.project import Project
from app.models.resource import Resource
from app.models.resource_allocation import ResourceAllocation
from app.models.risk import Risk
from app.models.task import Task
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
# Records that mentions and activity can point at, with the module that decides who sees them.
RECORD_MODULES: dict[str, tuple[Any, ProjectModule]] = {
    "TASK": (Task, ProjectModule.TASKS),
    "RISK": (Risk, ProjectModule.RISKS),
    "ISSUE": (Issue, ProjectModule.ISSUES),
    "ASSUMPTION": (Assumption, ProjectModule.ASSUMPTIONS),
    "DEPENDENCY": (Dependency, ProjectModule.DEPENDENCIES),
    "DECISION": (Decision, ProjectModule.DECISIONS),
    "CHANGE_REQUEST": (ChangeRequest, ProjectModule.CHANGE_REQUESTS),
}

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

        def where_visible(module: ProjectModule, among: list[UUID] = project_ids) -> list[UUID]:
            visible = access.visible_project_ids(module)
            return among if visible is None else [pid for pid in among if pid in visible]

        # Only the person's own work and their projects are loaded, not every record in the account.
        my_task_projects = self.tasks_assigned_to(current_user.id, where_visible(ProjectModule.TASKS))

        # "My projects": ones I manage (or created, while no manager is set) and ones where I have tasks.
        mine = set(my_task_projects.values())
        mine |= {
            project.id
            for project in projects
            if is_me(project.project_manager) or (not project.project_manager and project.created_by == current_user.id)
        }
        my_projects = [project for project in projects if project.id in mine]
        my_projects.sort(key=lambda project: project.updated_at, reverse=True)
        my_project_ids = [project.id for project in my_projects]

        # Every task in my projects (their progress and health), which includes all of my own tasks.
        task_rows = self.dashboard.list_tasks_for_projects(where_visible(ProjectModule.TASKS, my_project_ids))
        # Risks and issues in my projects (their health), plus the ones assigned to me anywhere.
        risk_rows = self.dashboard.list_risks_for_projects(
            where_visible(ProjectModule.RISKS),
            where=or_(Risk.project_id.in_(my_project_ids), self.assigned_to_me(Risk, current_user)),
        )
        issue_rows = self.dashboard.list_issues_for_projects(
            where_visible(ProjectModule.ISSUES),
            where=or_(Issue.project_id.in_(my_project_ids), self.assigned_to_me(Issue, current_user)),
        )
        assumption_rows = self.list_log_rows(
            Assumption, where_visible(ProjectModule.ASSUMPTIONS), where=self.named_like_me(Assumption.assigned_to_name, current_user)
        )
        dependency_rows = self.list_log_rows(
            Dependency, where_visible(ProjectModule.DEPENDENCIES), where=self.named_like_me(Dependency.assigned_to_name, current_user)
        )
        status_options = self.status_options(account_id)
        decision_rows = self.list_log_rows(
            Decision, where_visible(ProjectModule.DECISIONS), where=self.awaiting_approval(Decision, status_options, current_user)
        )
        change_request_rows = self.list_log_rows(
            ChangeRequest,
            where_visible(ProjectModule.CHANGE_REQUESTS),
            where=self.awaiting_approval(ChangeRequest, status_options, current_user),
        )

        my_tasks = [row for row in task_rows if row[0].id in my_task_projects]
        # Risks and issues can be assigned to a member or just by name; assumptions and dependencies only by name.
        my_risks = [row for row in risk_rows if row[0].assigned_to == current_user.id or is_me(row[0].assigned_to_name)]
        my_issues = [row for row in issue_rows if row[0].assigned_to == current_user.id or is_me(row[0].assigned_to_name)]
        my_assumptions = [row for row in assumption_rows if is_me(row[0].assigned_to_name)]
        my_dependencies = [row for row in dependency_rows if is_me(row[0].assigned_to_name)]

        complete_status_ids = self.complete_status_ids(status_options)
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

        project_statuses = self.hierarchy.get_status_values_by_ids(
            {project.status_id for project in my_projects if project.status_id is not None}
        )
        at_risk_ids = {
            item["id"]
            for item in self.dashboard_service.projects_at_risk(
                projects=my_projects,
                project_statuses=project_statuses,
                task_rows=task_rows,
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

        mentions = self.mentions(account_id=account_id, current_user=current_user)
        activity = self.recent_activity(account_id=account_id, access=access, projects=my_projects)
        # The name and project of each record those point at, if the person can see it.
        records = self.resolve_records(
            [(comment.entity_type, comment.entity_id) for comment in mentions]
            + [(entry.entity_type, entry.entity_id) for entry in activity],
            projects=projects,
            where_visible=where_visible,
        )

        notifications = self.notifications(
            current_user=current_user,
            is_me=is_me,
            my_tasks=my_tasks,
            decision_rows=decision_rows,
            change_request_rows=change_request_rows,
            mentions=mentions,
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
            "recent_activity": self.describe_recent_activity(account_id=account_id, entries=activity, records=records),
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
        current_user: User,
        is_me,
        my_tasks: list[DashboardTaskRow],
        decision_rows: list[LogRow],
        change_request_rows: list[LogRow],
        mentions: list[Comment],
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

    def mentions(self, *, account_id: UUID, current_user: User) -> list[Comment]:
        return list(
            self.db.execute(
                select(Comment)
                .join(CommentMention, CommentMention.comment_id == Comment.id)
                .where(CommentMention.mentioned_user_id == current_user.id, Comment.account_id == account_id)
                .order_by(Comment.created_at.desc())
                .limit(NOTIFICATION_LIMIT * 4)
            ).scalars().all()
        )

    def my_names(self, user: User) -> set[str]:
        """The person's names, lower-cased with whitespace collapsed."""
        return {
            " ".join(name.split()).lower()
            for name in (user.full_name, f"{user.first_name or ''} {user.last_name or ''}")
            if name and name.strip()
        }

    def name_matcher(self, user: User):
        """Whether a free-text name (an assignee, a project manager) refers to this person."""
        names = self.my_names(user)

        def is_me(value: str | None) -> bool:
            return bool(value) and " ".join(str(value).split()).lower() in names

        return is_me

    def named_like_me(self, column: Any, user: User) -> ColumnElement[bool]:
        """Rows whose free-text name contains every word of one of the person's names.

        A superset of what name_matcher accepts, so the database can skip everyone else's rows;
        name_matcher still makes the exact call.
        """
        names = self.my_names(user)
        if not names:
            return false()
        return or_(
            *(and_(*(func.lower(column).contains(word, autoescape=True) for word in name.split())) for name in names)
        )

    def assigned_to_me(self, model: Any, user: User) -> ColumnElement[bool]:
        return or_(model.assigned_to == user.id, self.named_like_me(model.assigned_to_name, user))

    def title_of(self, item: Any) -> str:
        return getattr(item, "title", None) or getattr(item, "description", None) or ""

    def list_log_rows(self, model: Any, project_ids: list[UUID], *, where: ColumnElement[bool]) -> list[LogRow]:
        if not project_ids:
            return []
        statement = (
            select(model, Project, OptionValue)
            .join(Project, Project.id == model.project_id)
            .outerjoin(OptionValue, OptionValue.id == model.status_id)
            .where(model.project_id.in_(project_ids), where)
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

    def tasks_assigned_to(self, user_id: UUID, project_ids: list[UUID]) -> dict[UUID, UUID]:
        """Task id -> project id for the person's tasks, assigned directly or through a resource linked to them."""
        if not project_ids:
            return {}
        assigned = select(TaskAssignment.task_id).where(TaskAssignment.user_id == user_id)
        allocated = (
            select(ResourceAllocation.task_id)
            .join(Resource, Resource.id == ResourceAllocation.resource_id)
            .where(Resource.user_id == user_id)
        )
        rows = self.db.execute(
            select(Task.id, Task.project_id).where(
                or_(Task.id.in_(assigned), Task.id.in_(allocated)),
                Task.project_id.in_(project_ids),
                Task.is_deleted.is_(False),
            )
        ).all()
        return {task_id: project_id for task_id, project_id in rows}

    def status_options(self, account_id: UUID) -> list[tuple[str, OptionValue]]:
        """The STATUS options of the record types Home shows, with the entity type each belongs to."""
        return [
            (entity_type, option)
            for entity_type, option in self.db.execute(
                select(OptionSet.entity_type, OptionValue)
                .join(OptionValue, OptionValue.option_set_id == OptionSet.id)
                .where(
                    OptionSet.account_id == account_id,
                    OptionSet.name == "STATUS",
                    OptionSet.entity_type.in_([*COMPLETE_STATUS_KEYS, "DECISION", "CHANGE_REQUEST"]),
                )
                .order_by(OptionValue.sort_order, OptionValue.id)
            ).all()
        ]

    def complete_status_ids(self, status_options: list[tuple[str, OptionValue]]) -> dict[str, UUID]:
        found: dict[str, UUID] = {}
        for entity_type, keys in COMPLETE_STATUS_KEYS.items():
            options = [option for row_type, option in status_options if row_type == entity_type and option.is_active]
            for key in keys:
                match = next((option for option in options if key in self.helpers.option_keys(option)), None)
                if match is not None:
                    found[entity_type] = match.id
                    break
        return found

    def awaiting_approval(
        self, model: Any, status_options: list[tuple[str, OptionValue]], user: User
    ) -> ColumnElement[bool]:
        """Records in a waiting-for-approval status that someone else raised."""
        awaiting_ids = [option.id for _type, option in status_options if self.helpers.option_keys(option) & AWAITING_APPROVAL_KEYS]
        if not awaiting_ids:
            return false()
        return and_(model.status_id.in_(awaiting_ids), or_(model.created_by.is_(None), model.created_by != user.id))

    def resolve_records(
        self,
        refs: Iterable[tuple[str, UUID]],
        *,
        projects: list[Project],
        where_visible,
    ) -> dict[UUID, tuple[str, Project]]:
        """The display name and project of each referenced record the person can see, one query per type."""
        projects_by_id = {project.id: project for project in projects}
        ids_by_type: dict[str, set[UUID]] = defaultdict(set)
        for entity_type, entity_id in refs:
            ids_by_type[entity_type.upper()].add(entity_id)

        records: dict[UUID, tuple[str, Project]] = {
            project_id: (projects_by_id[project_id].name, projects_by_id[project_id])
            for project_id in ids_by_type.pop("PROJECT", set())
            if project_id in projects_by_id
        }
        for entity_type, ids in ids_by_type.items():
            if entity_type not in RECORD_MODULES:
                continue
            model, module = RECORD_MODULES[entity_type]
            visible_project_ids = where_visible(module)
            if not visible_project_ids:
                continue
            statement = select(model).where(model.id.in_(ids), model.project_id.in_(visible_project_ids))
            if model is Task:
                statement = statement.where(Task.is_deleted.is_(False))
            for item in self.db.scalars(statement):
                name = item.name if model is Task else self.title_of(item)
                records[item.id] = (name, projects_by_id[item.project_id])
        return records

    def recent_activity(self, *, account_id: UUID, access, projects: list[Project]) -> list[ActivityLog]:
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
        return access.visible_activity(entries)[:ACTIVITY_LIMIT]

    def describe_recent_activity(
        self, *, account_id: UUID, entries: list[ActivityLog], records: dict[UUID, tuple[str, Project]]
    ) -> list[dict[str, object]]:
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
