from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, delete, or_, select, update
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginate_statement, sort_descending
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.sprint import Sprint
from app.models.task import Task
from app.models.task_assignment import TaskAssignment
from app.models.task_predecessor import TaskPredecessor


class TaskRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_task(self, **values: object) -> Task:
        task = Task(**values)
        self.db.add(task)
        self.db.flush()
        self.db.refresh(task)
        return task

    def get_task(self, task_id: UUID, *, include_deleted: bool = False) -> Task | None:
        statement = select(Task).where(Task.id == task_id)
        if not include_deleted:
            statement = statement.where(Task.is_deleted.is_(False))
        return self.db.scalar(statement)

    def get_tasks_by_ids(self, task_ids: Iterable[UUID], *, include_deleted: bool = False) -> dict[UUID, Task]:
        task_ids = list(task_ids)
        if not task_ids:
            return {}
        statement = select(Task).where(Task.id.in_(task_ids))
        if not include_deleted:
            statement = statement.where(Task.is_deleted.is_(False))
        return {task.id: task for task in self.db.scalars(statement).all()}

    def get_sprint(self, sprint_id: UUID) -> Sprint | None:
        return self.db.get(Sprint, sprint_id)

    def list_tasks_statement(
        self,
        project_id: UUID,
        *,
        status_id: UUID | None = None,
        task_type_id: UUID | None = None,
        sort: str = "sort_order",
    ) -> Select[tuple[Task]]:
        statement = select(Task).where(Task.project_id == project_id, Task.is_deleted.is_(False))
        if status_id is not None:
            statement = statement.where(Task.status_id == status_id)
        if task_type_id is not None:
            statement = statement.where(Task.task_type_id == task_type_id)

        sort_field = sort.removeprefix("-")
        sort_column = {
            "sort_order": Task.sort_order,
            "created_at": Task.created_at,
            "updated_at": Task.updated_at,
            "name": Task.name,
        }[sort_field]
        if sort_descending(sort):
            sort_column = sort_column.desc()
        return statement.order_by(sort_column, Task.name, Task.id)

    def list_tasks_for_project(
        self,
        project_id: UUID,
        *,
        status_id: UUID | None = None,
        task_type_id: UUID | None = None,
        sort: str = "sort_order",
    ) -> list[Task]:
        statement = self.list_tasks_statement(
            project_id,
            status_id=status_id,
            task_type_id=task_type_id,
            sort=sort,
        )
        return list(self.db.scalars(statement).all())

    def list_tasks_by_parent(self, *, project_id: UUID, parent_task_id: UUID | None) -> list[Task]:
        statement = select(Task).where(Task.project_id == project_id, Task.is_deleted.is_(False))
        if parent_task_id is None:
            statement = statement.where(Task.parent_task_id.is_(None))
        else:
            statement = statement.where(Task.parent_task_id == parent_task_id)
        statement = statement.order_by(Task.sort_order, Task.name, Task.id)
        return list(self.db.scalars(statement).all())

    def list_tasks_for_project_paginated(
        self,
        project_id: UUID,
        *,
        status_id: UUID | None = None,
        task_type_id: UUID | None = None,
        sort: str = "sort_order",
        pagination: PaginationParams,
    ) -> tuple[list[Task], int]:
        statement = self.list_tasks_statement(
            project_id,
            status_id=status_id,
            task_type_id=task_type_id,
            sort=sort,
        )
        items, total = paginate_statement(self.db, statement, pagination)
        return items, total

    def update_task(self, task: Task, changes: dict[str, object]) -> Task:
        for field, value in changes.items():
            setattr(task, field, value)
        self.db.add(task)
        self.db.flush()
        self.db.refresh(task)
        return task

    def clear_parent_for_children(self, task_ids: Iterable[UUID]) -> None:
        task_ids = list(task_ids)
        if not task_ids:
            return
        self.db.execute(
            update(Task)
            .where(Task.parent_task_id.in_(task_ids))
            .values(parent_task_id=None)
        )
        self.db.flush()

    def has_non_deleted_children(self, task_ids: Iterable[UUID]) -> bool:
        task_ids = list(task_ids)
        if not task_ids:
            return False
        statement = (
            select(Task.id)
            .where(
                Task.parent_task_id.in_(task_ids),
                Task.is_deleted.is_(False),
            )
            .limit(1)
        )
        return self.db.scalar(statement) is not None

    def soft_delete_tasks(self, tasks: Iterable[Task], *, deleted_by: UUID) -> None:
        deleted_at = datetime.now(UTC)
        for task in tasks:
            task.is_deleted = True
            task.deleted_at = deleted_at
            task.deleted_by = deleted_by
            self.db.add(task)
        self.db.flush()

    def delete_tasks(self, tasks: Iterable[Task]) -> None:
        for task in tasks:
            self.db.delete(task)
        self.db.flush()

    def delete_assignments_for_tasks(self, task_ids: Iterable[UUID]) -> None:
        task_ids = list(task_ids)
        if not task_ids:
            return
        self.db.execute(delete(TaskAssignment).where(TaskAssignment.task_id.in_(task_ids)))
        self.db.flush()

    def delete_predecessors_for_tasks(self, task_ids: Iterable[UUID]) -> None:
        task_ids = list(task_ids)
        if not task_ids:
            return
        self.db.execute(
            delete(TaskPredecessor).where(
                or_(
                    TaskPredecessor.task_id.in_(task_ids),
                    TaskPredecessor.predecessor_task_id.in_(task_ids),
                )
            )
        )
        self.db.flush()

    def create_assignment(self, **values: object) -> TaskAssignment:
        assignment = TaskAssignment(**values)
        self.db.add(assignment)
        self.db.flush()
        self.db.refresh(assignment)
        return assignment

    def get_assignment(self, assignment_id: UUID) -> TaskAssignment | None:
        return self.db.get(TaskAssignment, assignment_id)

    def delete_assignment(self, assignment: TaskAssignment) -> None:
        self.db.delete(assignment)
        self.db.flush()

    def list_assignments_for_tasks(self, task_ids: Iterable[UUID]) -> dict[UUID, list[TaskAssignment]]:
        task_ids = list(task_ids)
        if not task_ids:
            return {}
        statement = select(TaskAssignment).where(TaskAssignment.task_id.in_(task_ids))
        assignments_by_task: dict[UUID, list[TaskAssignment]] = {}
        for assignment in self.db.scalars(statement).all():
            assignments_by_task.setdefault(assignment.task_id, []).append(assignment)
        return assignments_by_task

    def create_predecessor(self, **values: object) -> TaskPredecessor:
        predecessor = TaskPredecessor(**values)
        self.db.add(predecessor)
        self.db.flush()
        self.db.refresh(predecessor)
        return predecessor

    def get_predecessor(self, predecessor_id: UUID) -> TaskPredecessor | None:
        return self.db.get(TaskPredecessor, predecessor_id)

    def delete_predecessor(self, predecessor: TaskPredecessor) -> None:
        self.db.delete(predecessor)
        self.db.flush()

    def list_predecessors_for_tasks(self, task_ids: Iterable[UUID]) -> dict[UUID, list[TaskPredecessor]]:
        task_ids = list(task_ids)
        if not task_ids:
            return {}
        statement = select(TaskPredecessor).where(TaskPredecessor.task_id.in_(task_ids))
        predecessors_by_task: dict[UUID, list[TaskPredecessor]] = {}
        for predecessor in self.db.scalars(statement).all():
            predecessors_by_task.setdefault(predecessor.task_id, []).append(predecessor)
        return predecessors_by_task

    def get_valid_task_option(
        self,
        *,
        account_id: UUID,
        option_name: str,
        option_value_id: UUID,
    ) -> OptionValue | None:
        statement = (
            select(OptionValue)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionValue.id == option_value_id,
                OptionSet.account_id == account_id,
                OptionSet.entity_type == "TASK",
                OptionSet.name == option_name,
                OptionValue.is_active.is_(True),
            )
        )
        return self.db.scalar(statement)

    def get_default_task_option_id(self, *, account_id: UUID, option_name: str) -> UUID | None:
        statement = (
            select(OptionValue.id)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionSet.account_id == account_id,
                OptionSet.entity_type == "TASK",
                OptionSet.name == option_name,
                OptionValue.is_active.is_(True),
                OptionValue.is_default.is_(True),
            )
            .order_by(OptionValue.sort_order, OptionValue.label)
            .limit(1)
        )
        return self.db.scalar(statement)

    def get_option_values_by_ids(self, option_value_ids: Iterable[UUID]) -> dict[UUID, OptionValue]:
        option_value_ids = list(option_value_ids)
        if not option_value_ids:
            return {}
        statement = select(OptionValue).where(OptionValue.id.in_(option_value_ids))
        return {option_value.id: option_value for option_value in self.db.scalars(statement).all()}

    def get_sprints_by_ids(self, sprint_ids: Iterable[UUID]) -> dict[UUID, Sprint]:
        sprint_ids = list(sprint_ids)
        if not sprint_ids:
            return {}
        statement = select(Sprint).where(Sprint.id.in_(sprint_ids))
        return {sprint.id: sprint for sprint in self.db.scalars(statement).all()}