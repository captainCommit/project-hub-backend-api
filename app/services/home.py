"""The signed-in person's home page: their dated work, the projects they're on, and recent activity there."""

from collections import defaultdict
from datetime import date
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.access_grant import ProjectModule
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
# Which status option marks an item done, in order of preference (matched on the option's value or label).
COMPLETE_STATUS_KEYS = {
    "TASK": ("complete", "completed", "done"),
    "RISK": ("closed", "resolved"),
    "ISSUE": ("closed", "resolved"),
}


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

        def where_visible(module: ProjectModule) -> list[UUID]:
            visible = access.visible_project_ids(module)
            return project_ids if visible is None else [pid for pid in project_ids if pid in visible]

        task_rows = self.dashboard.list_tasks_for_projects(where_visible(ProjectModule.TASKS))
        risk_rows = self.dashboard.list_risks_for_projects(where_visible(ProjectModule.RISKS))
        issue_rows = self.dashboard.list_issues_for_projects(where_visible(ProjectModule.ISSUES))

        my_task_ids = self.task_ids_assigned_to(current_user.id, [row[0].id for row in task_rows])
        my_tasks = [row for row in task_rows if row[0].id in my_task_ids]
        my_risks = [row for row in risk_rows if row[0].assigned_to == current_user.id]
        my_issues = [row for row in issue_rows if row[0].assigned_to == current_user.id]

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
        priorities.sort(key=lambda item: (item["due_date"], str(item["title"]).lower(), str(item["id"])))

        # "My projects": anything I have work on, plus projects I created.
        mine = {row[1].id for row in my_tasks} | {row[1].id for row in my_risks} | {row[1].id for row in my_issues}
        mine |= {project.id for project in projects if project.created_by == current_user.id}
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
        for project in my_projects:
            status_value = self.dashboard_service.status_for_project(project, project_statuses)
            is_completed = self.dashboard_service.is_completed_project_status(status_value)
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

        return {
            "priorities": priorities,
            "counts": {
                "all": len(priorities),
                "overdue": sum(1 for item in priorities if item["bucket"] == "OVERDUE"),
                "week": sum(1 for item in priorities if item["bucket"] == "WEEK"),
                "upcoming": sum(1 for item in priorities if item["bucket"] == "UPCOMING"),
            },
            "active_project_count": sum(1 for item in project_summaries if not item["is_completed"]),
            "my_projects": project_summaries[:PROJECT_LIMIT],
            "recent_activity": self.recent_activity(
                account_id=account_id,
                access=access,
                projects=my_projects,
                task_rows=task_rows,
                risk_rows=risk_rows,
                issue_rows=issue_rows,
            ),
        }

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
        task_rows: list,
        risk_rows: list,
        issue_rows: list,
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

        project_of: dict[UUID, Project] = {project.id: project for project in projects}
        for rows in (task_rows, risk_rows, issue_rows):
            for row in rows:
                project_of.setdefault(row[0].id, row[1])

        described = []
        for entry in describe_activity(self.db, account_id, entries):
            project = project_of.get(entry["entity_id"])
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
