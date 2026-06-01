from collections import defaultdict
import re
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models.account_member import AccountMemberRole
from app.models.option_value import OptionValue
from app.models.program import Program
from app.models.project import Project
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.hierarchy import HierarchyRepository


SPRINT_ACTIVE_KEYS = {"active"}
TASK_DONE_KEYS = {"done", "complete", "completed", "closed"}


class DeliveryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.hierarchy = HierarchyRepository(db)

    def get_delivery_overview(
        self,
        *,
        account_id: UUID,
        current_user: User,
        program_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> dict[str, object]:
        self.require_account_member(account_id=account_id, user_id=current_user.id)
        programs = self.filtered_programs(account_id=account_id, program_id=program_id, project_id=project_id)
        projects = self.filtered_projects(account_id=account_id, programs=programs, program_id=program_id, project_id=project_id)

        project_ids = [project.id for project in projects]
        sprints = self.list_sprints(project_ids)
        sprint_ids = [sprint.id for sprint in sprints]
        sprint_statuses = self.hierarchy.get_status_values_by_ids(
            {sprint.status_id for sprint in sprints if sprint.status_id is not None}
        )
        sprint_metrics = self.sprint_story_metrics(sprint_ids)

        sprints_by_project: dict[UUID, list[dict[str, object]]] = defaultdict(list)
        for sprint in sprints:
            metrics = sprint_metrics[sprint.id]
            status_summary = self.option_summary(sprint.status_id, sprint_statuses)
            sprints_by_project[sprint.project_id].append(
                {
                    "id": sprint.id,
                    "name": sprint.name,
                    "status_id": sprint.status_id,
                    "status": status_summary,
                    "start_date": sprint.start_date,
                    "end_date": sprint.end_date,
                    "total_stories": metrics["total_stories"],
                    "done_stories": metrics["done_stories"],
                    "total_story_points": metrics["total_story_points"],
                    "completed_story_points": metrics["completed_story_points"],
                }
            )

        projects_by_program: dict[UUID, list[Project]] = defaultdict(list)
        for project in projects:
            projects_by_program[project.program_id].append(project)

        total_sprints = 0
        active_sprints = 0
        total_stories = 0
        done_stories = 0
        program_summaries: list[dict[str, object]] = []

        for program in programs:
            project_summaries: list[dict[str, object]] = []
            program_total_sprints = 0
            program_active_sprints = 0
            program_total_stories = 0
            program_done_stories = 0

            for project in projects_by_program.get(program.id, []):
                project_sprints = sprints_by_project.get(project.id, [])
                project_total_sprints = len(project_sprints)
                project_active_sprints = sum(
                    1
                    for sprint_summary in project_sprints
                    if self.is_active_sprint_status(sprint_summary["status"])  # type: ignore[arg-type]
                )
                project_total_stories = sum(int(sprint_summary["total_stories"]) for sprint_summary in project_sprints)
                project_done_stories = sum(int(sprint_summary["done_stories"]) for sprint_summary in project_sprints)

                project_summaries.append(
                    {
                        "id": project.id,
                        "name": project.name,
                        "total_sprints": project_total_sprints,
                        "active_sprints": project_active_sprints,
                        "total_stories": project_total_stories,
                        "done_stories": project_done_stories,
                        "sprints": project_sprints,
                    }
                )
                program_total_sprints += project_total_sprints
                program_active_sprints += project_active_sprints
                program_total_stories += project_total_stories
                program_done_stories += project_done_stories

            program_summaries.append(
                {
                    "id": program.id,
                    "name": program.name,
                    "total_sprints": program_total_sprints,
                    "active_sprints": program_active_sprints,
                    "total_stories": program_total_stories,
                    "done_stories": program_done_stories,
                    "projects": project_summaries,
                }
            )
            total_sprints += program_total_sprints
            active_sprints += program_active_sprints
            total_stories += program_total_stories
            done_stories += program_done_stories

        return {
            "total_sprints": total_sprints,
            "active_sprints": active_sprints,
            "total_stories": total_stories,
            "done_stories": done_stories,
            "programs": program_summaries,
        }

    def filtered_programs(
        self,
        *,
        account_id: UUID,
        program_id: UUID | None,
        project_id: UUID | None,
    ) -> list[Program]:
        if program_id is not None:
            program = self.get_program_or_404(program_id)
            if program.account_id != account_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Program must belong to the account.")
            return [program]
        if project_id is not None:
            project = self.get_project_or_404(project_id)
            if project.account_id != account_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project must belong to the account.")
            return [self.get_program_or_404(project.program_id)]
        return self.hierarchy.list_programs_for_account(account_id)

    def filtered_projects(
        self,
        *,
        account_id: UUID,
        programs: list[Program],
        program_id: UUID | None,
        project_id: UUID | None,
    ) -> list[Project]:
        if project_id is not None:
            project = self.get_project_or_404(project_id)
            if project.account_id != account_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project must belong to the account.")
            if program_id is not None and project.program_id != program_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project must belong to the supplied program.")
            return [project]
        program_ids = {program.id for program in programs}
        return [project for project in self.hierarchy.list_projects_for_account(account_id) if project.program_id in program_ids]

    def list_sprints(self, project_ids: list[UUID]) -> list[Sprint]:
        if not project_ids:
            return []
        statement = (
            select(Sprint)
            .where(Sprint.project_id.in_(project_ids))
            .order_by(Sprint.start_date, Sprint.name, Sprint.id)
        )
        return list(self.db.scalars(statement).all())

    def sprint_story_metrics(self, sprint_ids: list[UUID]) -> dict[UUID, dict[str, int]]:
        metrics = {
            sprint_id: {
                "total_stories": 0,
                "done_stories": 0,
                "total_story_points": 0,
                "completed_story_points": 0,
            }
            for sprint_id in sprint_ids
        }
        if not sprint_ids:
            return metrics

        task_status = aliased(OptionValue)
        statement = (
            select(Task, task_status)
            .outerjoin(task_status, task_status.id == Task.status_id)
            .where(Task.sprint_id.in_(sprint_ids), Task.is_deleted.is_(False))
        )
        for task, status_value in self.db.execute(statement).all():
            if task.sprint_id is None:
                continue
            sprint_metrics = metrics[task.sprint_id]
            story_points = task.story_points or 0
            sprint_metrics["total_stories"] += 1
            sprint_metrics["total_story_points"] += story_points
            if self.is_done_task_status(status_value):
                sprint_metrics["done_stories"] += 1
                sprint_metrics["completed_story_points"] += story_points
        return metrics

    def option_summary(
        self,
        option_value_id: UUID | None,
        options: dict[UUID, OptionValue],
    ) -> dict[str, object] | None:
        if option_value_id is None or option_value_id not in options:
            return None
        option_value = options[option_value_id]
        return {
            "id": option_value.id,
            "label": option_value.label,
            "value": option_value.value,
            "color": option_value.color,
        }

    def is_active_sprint_status(self, status_summary: dict[str, object] | None) -> bool:
        if status_summary is None:
            return False
        return bool(
            {
                self.normalize_option_key(str(status_summary.get("value") or "")),
                self.normalize_option_key(str(status_summary.get("label") or "")),
            }
            & SPRINT_ACTIVE_KEYS
        )

    def is_done_task_status(self, status_value: OptionValue | None) -> bool:
        if status_value is None:
            return False
        return bool({self.normalize_option_key(status_value.value), self.normalize_option_key(status_value.label)} & TASK_DONE_KEYS)

    def normalize_option_key(self, value: str | None) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").lower())

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

    def require_account_role(self, *, account_id: UUID, user_id: UUID, allowed_roles: set[str]) -> None:
        self.require_account_member(account_id=account_id, user_id=user_id)
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None or membership.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")