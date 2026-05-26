from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginate_statement, sort_descending
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
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

    def get_task(self, task_id: UUID) -> Task | None:
        return self.db.get(Task, task_id)

    def list_tasks_statement(
        self,
        project_id: UUID,
        *,
        status_id: UUID | None = None,
        task_type_id: UUID | None = None,
        sort: str = "sort_order",
    ) -> Select[tuple[Task]]:
        statement = select(Task).where(Task.project_id == project_id)
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