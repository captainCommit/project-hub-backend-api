from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.sprint import Sprint
from app.models.task import Task


class SprintRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **values: object) -> Sprint:
        sprint = Sprint(**values)
        self.db.add(sprint)
        self.db.flush()
        self.db.refresh(sprint)
        return sprint

    def get(self, sprint_id: UUID) -> Sprint | None:
        return self.db.get(Sprint, sprint_id)

    def list_for_project(self, project_id: UUID) -> list[Sprint]:
        statement = select(Sprint).where(Sprint.project_id == project_id).order_by(Sprint.start_date, Sprint.name)
        return list(self.db.scalars(statement).all())

    def update(self, sprint: Sprint, changes: dict[str, object]) -> Sprint:
        for field, value in changes.items():
            setattr(sprint, field, value)
        self.db.add(sprint)
        self.db.flush()
        self.db.refresh(sprint)
        return sprint

    def get_valid_status(self, *, account_id: UUID, status_id: UUID) -> OptionValue | None:
        statement = (
            select(OptionValue)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionValue.id == status_id,
                OptionSet.account_id == account_id,
                OptionSet.entity_type == "SPRINT",
                OptionSet.name == "STATUS",
                OptionValue.is_active.is_(True),
            )
        )
        return self.db.scalar(statement)

    def get_default_status_id(self, *, account_id: UUID) -> UUID | None:
        statement = (
            select(OptionValue.id)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionSet.account_id == account_id,
                OptionSet.entity_type == "SPRINT",
                OptionSet.name == "STATUS",
                OptionValue.is_active.is_(True),
                OptionValue.is_default.is_(True),
            )
            .order_by(OptionValue.sort_order, OptionValue.label)
            .limit(1)
        )
        return self.db.scalar(statement)

    def get_status_values_by_ids(self, status_ids: Iterable[UUID]) -> dict[UUID, OptionValue]:
        status_ids = list(status_ids)
        if not status_ids:
            return {}
        statement = select(OptionValue).where(OptionValue.id.in_(status_ids))
        return {status.id: status for status in self.db.scalars(statement).all()}

    def list_tasks_with_status_for_sprint(self, sprint_id: UUID) -> list[tuple[Task, OptionValue | None]]:
        task_status = aliased(OptionValue)
        statement = (
            select(Task, task_status)
            .outerjoin(task_status, task_status.id == Task.status_id)
            .where(Task.sprint_id == sprint_id, Task.is_deleted.is_(False))
            .order_by(Task.sort_order, Task.name, Task.id)
        )
        return list(self.db.execute(statement).all())