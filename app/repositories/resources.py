from collections.abc import Iterable
from datetime import date
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.models.program import Program
from app.models.project import Project
from app.models.resource import Resource
from app.models.resource_allocation import ResourceAllocation
from app.models.resource_time_off import ResourceTimeOff
from app.models.task import Task


class ResourceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_resource(self, **values: object) -> Resource:
        resource = Resource(**values)
        self.db.add(resource)
        self.db.flush()
        self.db.refresh(resource)
        return resource

    def get_resource(self, resource_id: UUID) -> Resource | None:
        return self.db.get(Resource, resource_id)

    def list_active_resources_for_account(self, account_id: UUID) -> list[Resource]:
        statement = (
            select(Resource)
            .where(Resource.account_id == account_id, Resource.is_active.is_(True))
            .order_by(Resource.name, Resource.id)
        )
        return list(self.db.scalars(statement).all())

    def list_calendar_resources(self, *, account_id: UUID, resource_id: UUID | None = None) -> list[Resource]:
        statement = select(Resource).where(Resource.account_id == account_id)
        if resource_id is None:
            statement = statement.where(Resource.is_active.is_(True))
        else:
            statement = statement.where(Resource.id == resource_id)
        statement = statement.order_by(Resource.name, Resource.id)
        return list(self.db.scalars(statement).all())

    def update_resource(self, resource: Resource, changes: dict[str, object]) -> Resource:
        for field, value in changes.items():
            setattr(resource, field, value)
        self.db.add(resource)
        self.db.flush()
        self.db.refresh(resource)
        return resource

    def deactivate_resource(self, resource: Resource) -> Resource:
        resource.is_active = False
        self.db.add(resource)
        self.db.flush()
        self.db.refresh(resource)
        return resource

    def create_allocation(self, **values: object) -> ResourceAllocation:
        allocation = ResourceAllocation(**values)
        self.db.add(allocation)
        self.db.flush()
        self.db.refresh(allocation)
        return allocation

    def get_allocation(self, allocation_id: UUID) -> ResourceAllocation | None:
        return self.db.get(ResourceAllocation, allocation_id)

    def update_allocation(self, allocation: ResourceAllocation, changes: dict[str, object]) -> ResourceAllocation:
        for field, value in changes.items():
            setattr(allocation, field, value)
        self.db.add(allocation)
        self.db.flush()
        self.db.refresh(allocation)
        return allocation

    def delete_allocation(self, allocation: ResourceAllocation) -> None:
        self.db.delete(allocation)
        self.db.flush()

    def create_time_off(self, **values: object) -> ResourceTimeOff:
        time_off = ResourceTimeOff(**values)
        self.db.add(time_off)
        self.db.flush()
        self.db.refresh(time_off)
        return time_off

    def get_time_off(self, time_off_id: UUID) -> ResourceTimeOff | None:
        return self.db.get(ResourceTimeOff, time_off_id)

    def update_time_off(self, time_off: ResourceTimeOff, changes: dict[str, object]) -> ResourceTimeOff:
        for field, value in changes.items():
            setattr(time_off, field, value)
        self.db.add(time_off)
        self.db.flush()
        self.db.refresh(time_off)
        return time_off

    def delete_time_off(self, time_off: ResourceTimeOff) -> None:
        self.db.delete(time_off)
        self.db.flush()

    def list_allocations_for_resource(self, resource_id: UUID) -> list[ResourceAllocation]:
        statement = (
            select(ResourceAllocation)
            .where(ResourceAllocation.resource_id == resource_id)
            .order_by(ResourceAllocation.start_date, ResourceAllocation.created_at, ResourceAllocation.id)
        )
        return list(self.db.scalars(statement).all())

    def list_time_off_for_resource(self, resource_id: UUID) -> list[ResourceTimeOff]:
        statement = (
            select(ResourceTimeOff)
            .where(ResourceTimeOff.resource_id == resource_id)
            .order_by(ResourceTimeOff.start_date, ResourceTimeOff.created_at, ResourceTimeOff.id)
        )
        return list(self.db.scalars(statement).all())

    def list_calendar_allocations(
        self,
        *,
        account_id: UUID,
        start_date: date,
        end_date: date,
        resource_id: UUID | None = None,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
    ) -> list[tuple[ResourceAllocation, Resource, Task, Project, Program]]:
        statement: Select[tuple[ResourceAllocation, Resource, Task, Project, Program]] = (
            select(ResourceAllocation, Resource, Task, Project, Program)
            .join(Resource, Resource.id == ResourceAllocation.resource_id)
            .join(Task, Task.id == ResourceAllocation.task_id)
            .join(Project, Project.id == Task.project_id)
            .join(Program, Program.id == Project.program_id)
            .where(
                ResourceAllocation.account_id == account_id,
                Task.is_deleted.is_(False),
                or_(ResourceAllocation.start_date.is_(None), ResourceAllocation.start_date <= end_date),
                or_(ResourceAllocation.end_date.is_(None), ResourceAllocation.end_date >= start_date),
            )
            .order_by(Resource.name, ResourceAllocation.start_date, Task.name, ResourceAllocation.id)
        )
        if resource_id is not None:
            statement = statement.where(ResourceAllocation.resource_id == resource_id)
        if project_id is not None:
            statement = statement.where(Task.project_id == project_id)
        if program_id is not None:
            statement = statement.where(Project.program_id == program_id)
        return list(self.db.execute(statement).all())

    def list_calendar_time_off(
        self,
        *,
        account_id: UUID,
        start_date: date,
        end_date: date,
        resource_ids: Iterable[UUID],
    ) -> list[ResourceTimeOff]:
        resource_ids = list(resource_ids)
        if not resource_ids:
            return []
        statement = (
            select(ResourceTimeOff)
            .where(
                ResourceTimeOff.account_id == account_id,
                ResourceTimeOff.resource_id.in_(resource_ids),
                ResourceTimeOff.start_date <= end_date,
                ResourceTimeOff.end_date >= start_date,
            )
            .order_by(ResourceTimeOff.resource_id, ResourceTimeOff.start_date, ResourceTimeOff.id)
        )
        return list(self.db.scalars(statement).all())

    def get_resources_by_ids(self, resource_ids: Iterable[UUID]) -> dict[UUID, Resource]:
        resource_ids = list(resource_ids)
        if not resource_ids:
            return {}
        statement = select(Resource).where(Resource.id.in_(resource_ids))
        return {resource.id: resource for resource in self.db.scalars(statement).all()}