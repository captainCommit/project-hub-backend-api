from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginated_response, validate_sort
from app.models.account_member import AccountMemberRole
from app.models.option_value import OptionValue
from app.models.project import Project
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.task_assignment import TaskAssignment
from app.models.task_predecessor import TaskPredecessor
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.hierarchy import HierarchyRepository
from app.repositories.tasks import TaskRepository
from app.schemas.tasks import (
    TaskAssignmentCreate,
    TaskBulkDeleteRequest,
    TaskBulkUpdateRequest,
    TaskCreate,
    TaskMoveRequest,
    TaskPredecessorCreate,
    TaskReorderRequest,
    TaskUpdate,
)
from app.services.activity import ActivityLogService
from app.services.notifications import NotificationService
from app.models.notification import NotificationType


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
        self.validate_sprint(project=project, sprint_id=task_in.sprint_id)
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
        priority_id = None
        if task_in.priority_id is not None:
            priority_id = self.validate_task_option_id(
                account_id=project.account_id,
                option_name="PRIORITY",
                option_value_id=task_in.priority_id,
                detail="Invalid task priority.",
            )
        task = self.tasks.create_task(
            account_id=project.account_id,
            project_id=project.id,
            sprint_id=task_in.sprint_id,
            parent_task_id=task_in.parent_task_id,
            task_type_id=task_type_id,
            status_id=status_id,
            priority_id=priority_id,
            name=task_in.name,
            description=task_in.description,
            start_date=task_in.start_date,
            finish_date=task_in.finish_date,
            duration_days=task_in.duration_days,
            story_points=task_in.story_points,
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
        project = self.get_project_or_404(task.project_id)
        changes = task_in.model_dump(exclude_unset=True)
        self.prepare_task_update_changes(task=task, project=project, changes=changes)
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
        if "sprint_id" in changes:
            ActivityLogService(self.db).record(
                account_id=task.account_id,
                entity_type="TASK",
                entity_id=task.id,
                action="TASK_SPRINT_ASSIGNED",
                old_values={"sprint_id": old_values.get("sprint_id")},
                new_values={"sprint_id": task.sprint_id},
                created_by=current_user.id,
            )
        self.db.commit()
        self.db.refresh(task)
        return self.enrich_task(task)

    def bulk_update_tasks(
        self,
        *,
        project_id: UUID,
        bulk_in: TaskBulkUpdateRequest,
        current_user: User,
    ) -> list[dict[str, object]]:
        project = self.get_project_or_404(project_id)
        self.require_account_role(
            account_id=project.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        task_ids = [update.id for update in bulk_in.updates]
        if len(set(task_ids)) != len(task_ids):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Duplicate task update id.")
        tasks_by_id = self.tasks.get_tasks_by_ids(task_ids)
        if len(tasks_by_id) != len(task_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
        for task in tasks_by_id.values():
            if task.project_id != project.id or task.account_id != project.account_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="All tasks must belong to the project.",
                )

        updated_tasks: list[Task] = []
        try:
            for update in bulk_in.updates:
                task = tasks_by_id[update.id]
                fields_set = update.fields.model_fields_set
                changes = update.fields.model_dump(exclude_unset=True)
                assigned_to_supplied = "assigned_to" in fields_set
                assigned_to = changes.pop("assigned_to", None)
                self.prepare_task_update_changes(task=task, project=project, changes=changes)
                old_values = {field: getattr(task, field) for field in changes}
                if assigned_to_supplied:
                    old_values["assigned_to"] = self.replace_task_user_assignment(task=task, assigned_to=assigned_to)
                if changes:
                    task = self.tasks.update_task(task, changes)
                new_values = {field: getattr(task, field) for field in changes}
                if assigned_to_supplied:
                    new_values["assigned_to"] = assigned_to
                if old_values or new_values:
                    ActivityLogService(self.db).record(
                        account_id=task.account_id,
                        entity_type="TASK",
                        entity_id=task.id,
                        action="UPDATED",
                        old_values=old_values,
                        new_values=new_values,
                        created_by=current_user.id,
                    )
                if "sprint_id" in changes:
                    ActivityLogService(self.db).record(
                        account_id=task.account_id,
                        entity_type="TASK",
                        entity_id=task.id,
                        action="TASK_SPRINT_ASSIGNED",
                        old_values={"sprint_id": old_values.get("sprint_id")},
                        new_values={"sprint_id": task.sprint_id},
                        created_by=current_user.id,
                    )
                updated_tasks.append(task)
            self.db.commit()
            for task in updated_tasks:
                self.db.refresh(task)
            return self.enrich_tasks(updated_tasks)
        except Exception:
            self.db.rollback()
            raise

    def bulk_delete_tasks(
        self,
        *,
        project_id: UUID,
        bulk_in: TaskBulkDeleteRequest,
        current_user: User,
    ) -> None:
        project = self.get_project_or_404(project_id)
        self.require_account_role(
            account_id=project.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        task_ids = bulk_in.task_ids
        if len(set(task_ids)) != len(task_ids):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Duplicate task id.")
        tasks_by_id = self.tasks.get_tasks_by_ids(task_ids)
        if len(tasks_by_id) != len(task_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
        tasks_to_delete = [tasks_by_id[task_id] for task_id in task_ids]
        for task in tasks_to_delete:
            if task.project_id != project.id or task.account_id != project.account_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="All tasks must belong to the project.",
                )
        if self.tasks.has_non_deleted_children(task_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete task with non-deleted children.",
            )

        try:
            for task in tasks_to_delete:
                ActivityLogService(self.db).record(
                    account_id=task.account_id,
                    entity_type="TASK",
                    entity_id=task.id,
                    action="TASK_DELETED",
                    old_values={
                        "name": task.name,
                        "project_id": task.project_id,
                        "parent_task_id": task.parent_task_id,
                        "sort_order": task.sort_order,
                        "status_id": task.status_id,
                    },
                    new_values={"is_deleted": True, "deleted_by": current_user.id},
                    created_by=current_user.id,
                )
            self.tasks.soft_delete_tasks(tasks_to_delete, deleted_by=current_user.id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def delete_task(self, *, task_id: UUID, current_user: User) -> None:
        task = self.get_task_or_404(task_id)
        self.require_account_role(
            account_id=task.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        if self.tasks.has_non_deleted_children([task.id]):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete task with non-deleted children.",
            )

        try:
            ActivityLogService(self.db).record(
                account_id=task.account_id,
                entity_type="TASK",
                entity_id=task.id,
                action="TASK_DELETED",
                old_values={
                    "name": task.name,
                    "project_id": task.project_id,
                    "parent_task_id": task.parent_task_id,
                    "sort_order": task.sort_order,
                    "status_id": task.status_id,
                },
                new_values={"is_deleted": True, "deleted_by": current_user.id},
                created_by=current_user.id,
            )
            self.tasks.soft_delete_tasks([task], deleted_by=current_user.id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def reorder_tasks(
        self,
        *,
        project_id: UUID,
        reorder_in: TaskReorderRequest,
        current_user: User,
    ) -> list[dict[str, object]]:
        project = self.get_project_or_404(project_id)
        self.require_account_role(
            account_id=project.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        task_ids = [item.id for item in reorder_in.tasks]
        if len(set(task_ids)) != len(task_ids):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Duplicate task reorder id.")
        tasks_by_id = self.tasks.get_tasks_by_ids(task_ids)
        if len(tasks_by_id) != len(task_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
        for task in tasks_by_id.values():
            if task.project_id != project.id or task.account_id != project.account_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="All tasks must belong to the project.",
                )

        parent_overrides = {item.id: item.parent_task_id for item in reorder_in.tasks}
        for item in reorder_in.tasks:
            self.assert_valid_parent(
                project_id=project.id,
                task_id=item.id,
                new_parent_task_id=item.parent_task_id,
                parent_overrides=parent_overrides,
            )

        updated_tasks: list[Task] = []
        try:
            for item in reorder_in.tasks:
                task = tasks_by_id[item.id]
                task = self.update_task_hierarchy(
                    task=task,
                    changes={"parent_task_id": item.parent_task_id, "sort_order": item.sort_order},
                    action="TASK_REORDERED",
                    current_user=current_user,
                )
                updated_tasks.append(task)
            self.db.commit()
            for task in updated_tasks:
                self.db.refresh(task)
            return self.enrich_tasks(updated_tasks)
        except Exception:
            self.db.rollback()
            raise

    def move_task(self, *, task_id: UUID, move_in: TaskMoveRequest, current_user: User) -> dict[str, object]:
        task = self.get_task_or_404(task_id)
        self.require_account_role(
            account_id=task.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        self.assert_valid_parent(
            project_id=task.project_id,
            task_id=task.id,
            new_parent_task_id=move_in.parent_task_id,
        )
        try:
            task = self.update_task_hierarchy(
                task=task,
                changes={"parent_task_id": move_in.parent_task_id, "sort_order": move_in.sort_order},
                action="TASK_MOVED",
                current_user=current_user,
            )
            self.db.commit()
            self.db.refresh(task)
            return self.enrich_task(task)
        except Exception:
            self.db.rollback()
            raise

    def indent_task(self, *, task_id: UUID, current_user: User) -> dict[str, object]:
        task = self.get_task_or_404(task_id)
        self.require_account_role(
            account_id=task.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        previous_sibling = self.previous_sibling(task)
        if previous_sibling is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot indent task without a previous sibling.",
            )
        self.assert_valid_parent(
            project_id=task.project_id,
            task_id=task.id,
            new_parent_task_id=previous_sibling.id,
        )
        try:
            task = self.update_task_hierarchy(
                task=task,
                changes={
                    "parent_task_id": previous_sibling.id,
                    "sort_order": self.next_child_sort_order(project_id=task.project_id, parent_task_id=previous_sibling.id),
                },
                action="TASK_INDENTED",
                current_user=current_user,
            )
            self.db.commit()
            self.db.refresh(task)
            return self.enrich_task(task)
        except Exception:
            self.db.rollback()
            raise

    def outdent_task(self, *, task_id: UUID, current_user: User) -> dict[str, object]:
        task = self.get_task_or_404(task_id)
        self.require_account_role(
            account_id=task.account_id,
            user_id=current_user.id,
            allowed_roles=TASK_WRITE_ROLES,
        )
        if task.parent_task_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot outdent a root task.")
        old_parent = self.get_task_or_404(task.parent_task_id)
        self.assert_valid_parent(
            project_id=task.project_id,
            task_id=task.id,
            new_parent_task_id=old_parent.parent_task_id,
        )
        try:
            task = self.update_task_hierarchy(
                task=task,
                changes={
                    "parent_task_id": old_parent.parent_task_id,
                    "sort_order": self.sort_order_after_task(old_parent),
                },
                action="TASK_OUTDENTED",
                current_user=current_user,
            )
            self.db.commit()
            self.db.refresh(task)
            return self.enrich_task(task)
        except Exception:
            self.db.rollback()
            raise

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
        if assignment.user_id is not None:
            NotificationService(self.db).create_notification(
                account_id=assignment.account_id,
                user_id=assignment.user_id,
                entity_type="TASK",
                entity_id=task.id,
                notification_type=NotificationType.TASK_ASSIGNED,
                title="Task assigned to you",
                message=f"You were assigned to task {task.name}.",
                actor_user_id=current_user.id,
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

    def prepare_task_update_changes(self, *, task: Task, project: Project, changes: dict[str, object]) -> None:
        if "parent_task_id" in changes:
            self.assert_valid_parent(
                project_id=task.project_id,
                task_id=task.id,
                new_parent_task_id=changes["parent_task_id"],  # type: ignore[arg-type]
            )
        if "sprint_id" in changes and changes["sprint_id"] is not None:
            changes["sprint_id"] = self.validate_sprint(project=project, sprint_id=changes["sprint_id"])  # type: ignore[arg-type]
        if "status_id" in changes and changes["status_id"] is not None:
            changes["status_id"] = self.validate_task_option_id(
                account_id=task.account_id,
                option_name="STATUS",
                option_value_id=changes["status_id"],  # type: ignore[arg-type]
                detail="Invalid task status.",
            )
        if "task_type_id" in changes and changes["task_type_id"] is not None:
            changes["task_type_id"] = self.validate_task_option_id(
                account_id=task.account_id,
                option_name="TYPE",
                option_value_id=changes["task_type_id"],  # type: ignore[arg-type]
                detail="Invalid task type.",
            )
        if "priority_id" in changes and changes["priority_id"] is not None:
            changes["priority_id"] = self.validate_task_option_id(
                account_id=task.account_id,
                option_name="PRIORITY",
                option_value_id=changes["priority_id"],  # type: ignore[arg-type]
                detail="Invalid task priority.",
            )

    def replace_task_user_assignment(self, *, task: Task, assigned_to: object) -> object:
        existing_assignments = self.tasks.list_assignments_for_tasks([task.id]).get(task.id, [])
        existing_user_ids = [assignment.user_id for assignment in existing_assignments if assignment.user_id is not None]
        for assignment in existing_assignments:
            if assignment.user_id is not None:
                self.tasks.delete_assignment(assignment)
        if assigned_to is not None:
            self.tasks.create_assignment(
                account_id=task.account_id,
                task_id=task.id,
                user_id=assigned_to,
                resource_name=None,
            )
        if not existing_user_ids:
            return None
        if len(existing_user_ids) == 1:
            return existing_user_ids[0]
        return existing_user_ids

    def assert_valid_parent(
        self,
        *,
        project_id: UUID,
        task_id: UUID,
        new_parent_task_id: UUID | None,
        parent_overrides: dict[UUID, UUID | None] | None = None,
    ) -> None:
        if new_parent_task_id is None:
            return
        if new_parent_task_id == task_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task cannot be its own parent.")
        parent_task = self.get_task_or_404(new_parent_task_id)
        if parent_task.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent task must belong to the same project.",
            )

        visited: set[UUID] = set()
        current_parent_id: UUID | None = new_parent_task_id
        while current_parent_id is not None:
            if current_parent_id == task_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Task hierarchy cannot contain circular parent references.",
                )
            if current_parent_id in visited:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Task hierarchy cannot contain circular parent references.",
                )
            visited.add(current_parent_id)
            if parent_overrides is not None and current_parent_id in parent_overrides:
                current_parent_id = parent_overrides[current_parent_id]
                continue
            current_parent = self.get_task_or_404(current_parent_id)
            if current_parent.project_id != project_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Parent task must belong to the same project.",
                )
            current_parent_id = current_parent.parent_task_id

    def update_task_hierarchy(
        self,
        *,
        task: Task,
        changes: dict[str, object],
        action: str,
        current_user: User,
    ) -> Task:
        old_values = {field: getattr(task, field) for field in changes}
        task = self.tasks.update_task(task, changes)
        ActivityLogService(self.db).record(
            account_id=task.account_id,
            entity_type="TASK",
            entity_id=task.id,
            action=action,
            old_values=old_values,
            new_values={field: getattr(task, field) for field in changes},
            created_by=current_user.id,
        )
        return task

    def previous_sibling(self, task: Task) -> Task | None:
        siblings = self.tasks.list_tasks_by_parent(project_id=task.project_id, parent_task_id=task.parent_task_id)
        previous: Task | None = None
        for sibling in siblings:
            if sibling.id == task.id:
                return previous
            previous = sibling
        preceding_siblings = [sibling for sibling in siblings if sibling.id != task.id and sibling.sort_order < task.sort_order]
        return preceding_siblings[-1] if preceding_siblings else None

    def next_child_sort_order(self, *, project_id: UUID, parent_task_id: UUID) -> Decimal:
        children = self.tasks.list_tasks_by_parent(project_id=project_id, parent_task_id=parent_task_id)
        if not children:
            return Decimal("1")
        return max(child.sort_order for child in children) + Decimal("1")

    def sort_order_after_task(self, task: Task) -> Decimal:
        return task.sort_order + Decimal("0.01")

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

    def validate_sprint(self, *, project: Project, sprint_id: UUID | None) -> UUID | None:
        if sprint_id is None:
            return None
        sprint = self.tasks.get_sprint(sprint_id)
        if sprint is None or sprint.account_id != project.account_id or sprint.project_id != project.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sprint must belong to the same project.",
            )
        return sprint.id

    def enrich_task(self, task: Task) -> dict[str, object]:
        return self.enrich_tasks([task])[0]

    def enrich_tasks(self, tasks: list[Task]) -> list[dict[str, object]]:
        task_ids = [task.id for task in tasks]
        option_ids = {
            option_id
            for task in tasks
            for option_id in (task.status_id, task.task_type_id, task.priority_id)
            if option_id is not None
        }
        options = self.tasks.get_option_values_by_ids(option_ids)
        sprints = self.tasks.get_sprints_by_ids({task.sprint_id for task in tasks if task.sprint_id is not None})
        assignments = self.tasks.list_assignments_for_tasks(task_ids)
        predecessors = self.tasks.list_predecessors_for_tasks(task_ids)
        return [
            {
                **task.__dict__,
                "status": self.option_summary(task.status_id, options),
                "task_type": self.option_summary(task.task_type_id, options),
                "priority": self.option_summary(task.priority_id, options),
                "sprint": self.sprint_summary(task.sprint_id, sprints),
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

    def sprint_summary(self, sprint_id: UUID | None, sprints: dict[UUID, Sprint]) -> dict[str, object] | None:
        if sprint_id is None or sprint_id not in sprints:
            return None
        sprint = sprints[sprint_id]
        return {
            "id": sprint.id,
            "name": sprint.name,
            "status": None,
            "start_date": sprint.start_date,
            "end_date": sprint.end_date,
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