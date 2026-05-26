from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginated_response, validate_sort
from app.models.account_member import AccountMemberRole
from app.models.option_value import OptionValue
from app.models.project import Project
from app.models.task import Task
from app.models.task_assignment import TaskAssignment
from app.models.task_predecessor import TaskPredecessor
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.hierarchy import HierarchyRepository
from app.repositories.tasks import TaskRepository
from app.schemas.tasks import TaskAssignmentCreate, TaskCreate, TaskPredecessorCreate, TaskUpdate
from app.services.activity import ActivityLogService


TASK_WRITE_ROLES = {
    AccountMemberRole.OWNER.value,
    AccountMemberRole.ADMIN.value,
    AccountMemberRole.MANAGER.value,
    AccountMemberRole.MEMBER.value,
}


class TaskService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.hierarchy = HierarchyRepository(db)
        self.tasks = TaskRepository(db)

    def list_tasks(
        self,
        *,
        project_id: UUID,
        current_user: User,
        status_id: UUID | None = None,
        task_type_id: UUID | None = None,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[dict[str, object]] | dict[str, object]:
        project = self.get_project_or_404(project_id)
        self.require_account_member(account_id=project.account_id, user_id=current_user.id)
        sort_value = validate_sort(
            sort,
            allowed_fields={"sort_order", "created_at", "updated_at", "name"},
            default="sort_order",
        )
        if pagination and pagination.paginated:
            tasks, total = self.tasks.list_tasks_for_project_paginated(
                project_id,
                status_id=status_id,
                task_type_id=task_type_id,
                sort=sort_value,
                pagination=pagination,
            )
            return paginated_response(items=self.enrich_tasks(tasks), total=total, pagination=pagination)

        tasks = self.tasks.list_tasks_for_project(
            project_id,
            status_id=status_id,
            task_type_id=task_type_id,
            sort=sort_value,
        )
        return self.enrich_tasks(tasks)

    def get_task(self, *, task_id: UUID, current_user: User) -> dict[str, object]:
        task = self.get_task_or_404(task_id)
        self.require_account_member(account_id=task.account_id, user_id=current_user.id)
        return self.enrich_task(task)

    def create_task(self, *, project_id: UUID, task_in: TaskCreate, current_user: User) -> dict[str, object]:
        project = self.get_project_or_404(project_id)
        self.require_account_role(
            account_id=project.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        self.validate_parent_task(project_id=project_id, parent_task_id=task_in.parent_task_id)
        status_id = self.resolve_task_option_id(
            account_id=project.account_id,
            option_name="STATUS",
            option_value_id=task_in.status_id,
            detail="Invalid task status.",
        )
        task_type_id = self.resolve_task_option_id(
            account_id=project.account_id,
            option_name="TYPE",
            option_value_id=task_in.task_type_id,
            detail="Invalid task type.",
        )
        task = self.tasks.create_task(
            account_id=project.account_id,
            project_id=project.id,
            parent_task_id=task_in.parent_task_id,
            task_type_id=task_type_id,
            status_id=status_id,
            name=task_in.name,
            description=task_in.description,
            start_date=task_in.start_date,
            finish_date=task_in.finish_date,
            duration_days=task_in.duration_days,
            percent_complete=task_in.percent_complete,
            sort_order=task_in.sort_order,
            created_by=current_user.id,
        )
        ActivityLogService(self.db).record(
            account_id=task.account_id,
            entity_type="TASK",
            entity_id=task.id,
            action="CREATED",
            new_values={"name": task.name, "project_id": task.project_id, "status_id": task.status_id},
            created_by=current_user.id,
        )
        self.db.commit()
        self.db.refresh(task)
        return self.enrich_task(task)

    def update_task(self, *, task_id: UUID, task_in: TaskUpdate, current_user: User) -> dict[str, object]:
        task = self.get_task_or_404(task_id)
        self.require_account_role(
            account_id=task.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        changes = task_in.model_dump(exclude_unset=True)
        if "parent_task_id" in changes:
            parent_task_id = changes["parent_task_id"]
            if parent_task_id == task.id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task cannot be its own parent.")
            self.validate_parent_task(project_id=task.project_id, parent_task_id=parent_task_id)
        if "status_id" in changes and changes["status_id"] is not None:
            changes["status_id"] = self.validate_task_option_id(
                account_id=task.account_id,
                option_name="STATUS",
                option_value_id=changes["status_id"],
                detail="Invalid task status.",
            )
        if "task_type_id" in changes and changes["task_type_id"] is not None:
            changes["task_type_id"] = self.validate_task_option_id(
                account_id=task.account_id,
                option_name="TYPE",
                option_value_id=changes["task_type_id"],
                detail="Invalid task type.",
            )
        old_values = {field: getattr(task, field) for field in changes}
        task = self.tasks.update_task(task, changes)
        ActivityLogService(self.db).record(
            account_id=task.account_id,
            entity_type="TASK",
            entity_id=task.id,
            action="UPDATED",
            old_values=old_values,
            new_values={field: getattr(task, field) for field in changes},
            created_by=current_user.id,
        )
        self.db.commit()
        self.db.refresh(task)
        return self.enrich_task(task)

    def create_assignment(
        self,
        *,
        task_id: UUID,
        assignment_in: TaskAssignmentCreate,
        current_user: User,
    ) -> TaskAssignment:
        task = self.get_task_or_404(task_id)
        self.require_account_role(
            account_id=task.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        assignment = self.tasks.create_assignment(
            account_id=task.account_id,
            task_id=task.id,
            user_id=assignment_in.user_id,
            resource_name=assignment_in.resource_name,
        )
        self.db.commit()
        self.db.refresh(assignment)
        return assignment

    def delete_assignment(self, *, task_id: UUID, assignment_id: UUID, current_user: User) -> None:
        task = self.get_task_or_404(task_id)
        self.require_account_role(
            account_id=task.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        assignment = self.tasks.get_assignment(assignment_id)
        if assignment is None or assignment.task_id != task.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task assignment not found.")
        self.tasks.delete_assignment(assignment)
        self.db.commit()

    def create_predecessor(
        self,
        *,
        task_id: UUID,
        predecessor_in: TaskPredecessorCreate,
        current_user: User,
    ) -> TaskPredecessor:
        task = self.get_task_or_404(task_id)
        self.require_account_role(
            account_id=task.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        predecessor_task = self.get_task_or_404(predecessor_in.predecessor_task_id)
        if predecessor_task.id == task.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task cannot be its own predecessor.")
        if predecessor_task.project_id != task.project_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Predecessor task must belong to the same project.",
            )
        try:
            predecessor = self.tasks.create_predecessor(
                account_id=task.account_id,
                task_id=task.id,
                predecessor_task_id=predecessor_task.id,
                dependency_type=predecessor_in.dependency_type,
                lag_days=predecessor_in.lag_days,
            )
            self.db.commit()
            self.db.refresh(predecessor)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Task predecessor already exists.",
            ) from exc
        return predecessor

    def delete_predecessor(self, *, task_id: UUID, predecessor_id: UUID, current_user: User) -> None:
        task = self.get_task_or_404(task_id)
        self.require_account_role(
            account_id=task.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        predecessor = self.tasks.get_predecessor(predecessor_id)
        if predecessor is None or predecessor.task_id != task.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task predecessor not found.")
        self.tasks.delete_predecessor(predecessor)
        self.db.commit()

    def build_task_tree(self, *, project_id: UUID, current_user: User) -> list[dict[str, object]]:
        flat_tasks = self.list_tasks(project_id=project_id, current_user=current_user)
        if isinstance(flat_tasks, dict):
            flat_tasks = flat_tasks["items"]  # Defensive; tree endpoint does not request pagination.
        tasks_by_id = {task["id"]: {**task, "children": []} for task in flat_tasks}
        roots: list[dict[str, object]] = []
        for task in tasks_by_id.values():
            parent_task_id = task["parent_task_id"]
            if parent_task_id and parent_task_id in tasks_by_id:
                tasks_by_id[parent_task_id]["children"].append(task)
            else:
                roots.append(task)
        return roots

    def resolve_task_option_id(
        self,
        *,
        account_id: UUID,
        option_name: str,
        option_value_id: UUID | None,
        detail: str,
    ) -> UUID | None:
        if option_value_id is None:
            return self.tasks.get_default_task_option_id(account_id=account_id, option_name=option_name)
        return self.validate_task_option_id(
            account_id=account_id,
            option_name=option_name,
            option_value_id=option_value_id,
            detail=detail,
        )

    def validate_task_option_id(
        self,
        *,
        account_id: UUID,
        option_name: str,
        option_value_id: UUID,
        detail: str,
    ) -> UUID:
        option_value = self.tasks.get_valid_task_option(
            account_id=account_id,
            option_name=option_name,
            option_value_id=option_value_id,
        )
        if option_value is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
        return option_value.id

    def validate_parent_task(self, *, project_id: UUID, parent_task_id: UUID | None) -> None:
        if parent_task_id is None:
            return
        parent_task = self.get_task_or_404(parent_task_id)
        if parent_task.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent task must belong to the same project.",
            )

    def enrich_task(self, task: Task) -> dict[str, object]:
        return self.enrich_tasks([task])[0]

    def enrich_tasks(self, tasks: list[Task]) -> list[dict[str, object]]:
        task_ids = [task.id for task in tasks]
        option_ids = {
            option_id
            for task in tasks
            for option_id in (task.status_id, task.task_type_id)
            if option_id is not None
        }
        options = self.tasks.get_option_values_by_ids(option_ids)
        assignments = self.tasks.list_assignments_for_tasks(task_ids)
        predecessors = self.tasks.list_predecessors_for_tasks(task_ids)
        return [
            {
                **task.__dict__,
                "status": self.option_summary(task.status_id, options),
                "task_type": self.option_summary(task.task_type_id, options),
                "assignments": assignments.get(task.id, []),
                "predecessors": predecessors.get(task.id, []),
            }
            for task in tasks
        ]

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

    def get_project_or_404(self, project_id: UUID) -> Project:
        project = self.hierarchy.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
        return project

    def get_task_or_404(self, task_id: UUID) -> Task:
        task = self.tasks.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
        return task

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