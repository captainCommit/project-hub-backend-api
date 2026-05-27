from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.account_member import AccountMemberRole
from app.models.program import Program
from app.models.project import Project
from app.models.resource import Resource
from app.models.resource_allocation import ResourceAllocation
from app.models.task import Task
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.hierarchy import HierarchyRepository
from app.repositories.resources import ResourceRepository
from app.repositories.tasks import TaskRepository
from app.schemas.resources import ResourceAllocationCreate, ResourceAllocationUpdate, ResourceCreate, ResourceUpdate
from app.services.activity import ActivityLogService


RESOURCE_WRITE_ROLES = {
    AccountMemberRole.OWNER.value,
    AccountMemberRole.ADMIN.value,
    AccountMemberRole.MANAGER.value,
}

RESOURCE_ALLOCATION_WRITE_ROLES = {
    AccountMemberRole.OWNER.value,
    AccountMemberRole.ADMIN.value,
    AccountMemberRole.MANAGER.value,
    AccountMemberRole.MEMBER.value,
}


class ResourceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.hierarchy = HierarchyRepository(db)
        self.resources = ResourceRepository(db)
        self.tasks = TaskRepository(db)

    def list_resources(self, *, account_id: UUID, current_user: User) -> list[Resource]:
        self.require_account_member(account_id=account_id, user_id=current_user.id)
        return self.resources.list_active_resources_for_account(account_id)

    def create_resource(self, *, account_id: UUID, resource_in: ResourceCreate, current_user: User) -> Resource:
        self.require_account_role(
            account_id=account_id,
            user_id=current_user.id,
            allowed_roles=RESOURCE_WRITE_ROLES,
        )
        self.validate_user_belongs_to_account(account_id=account_id, user_id=resource_in.user_id)
        try:
            resource = self.resources.create_resource(
                account_id=account_id,
                user_id=resource_in.user_id,
                name=resource_in.name,
                role=resource_in.role,
                weekly_capacity_hours=resource_in.weekly_capacity_hours,
                created_by=current_user.id,
            )
            ActivityLogService(self.db).record(
                account_id=account_id,
                entity_type="RESOURCE",
                entity_id=resource.id,
                action="RESOURCE_CREATED",
                new_values=self.resource_activity_values(resource),
                created_by=current_user.id,
            )
            self.db.commit()
            self.db.refresh(resource)
            return resource
        except Exception:
            self.db.rollback()
            raise

    def get_resource(self, *, resource_id: UUID, current_user: User) -> Resource:
        resource = self.get_resource_or_404(resource_id)
        self.require_account_member(account_id=resource.account_id, user_id=current_user.id)
        return resource

    def update_resource(self, *, resource_id: UUID, resource_in: ResourceUpdate, current_user: User) -> Resource:
        resource = self.get_resource_or_404(resource_id)
        self.require_account_role(
            account_id=resource.account_id,
            user_id=current_user.id,
            allowed_roles=RESOURCE_WRITE_ROLES,
        )
        changes = resource_in.model_dump(exclude_unset=True)
        if "user_id" in changes:
            self.validate_user_belongs_to_account(account_id=resource.account_id, user_id=changes["user_id"])  # type: ignore[arg-type]
        if not changes:
            return resource

        old_values = {field: getattr(resource, field) for field in changes}
        try:
            resource = self.resources.update_resource(resource, changes)
            ActivityLogService(self.db).record(
                account_id=resource.account_id,
                entity_type="RESOURCE",
                entity_id=resource.id,
                action="RESOURCE_UPDATED",
                old_values=old_values,
                new_values={field: getattr(resource, field) for field in changes},
                created_by=current_user.id,
            )
            self.db.commit()
            self.db.refresh(resource)
            return resource
        except Exception:
            self.db.rollback()
            raise

    def delete_resource(self, *, resource_id: UUID, current_user: User) -> None:
        resource = self.get_resource_or_404(resource_id)
        self.require_account_role(
            account_id=resource.account_id,
            user_id=current_user.id,
            allowed_roles=RESOURCE_WRITE_ROLES,
        )
        if not resource.is_active:
            return
        try:
            old_values = self.resource_activity_values(resource)
            resource = self.resources.deactivate_resource(resource)
            ActivityLogService(self.db).record(
                account_id=resource.account_id,
                entity_type="RESOURCE",
                entity_id=resource.id,
                action="RESOURCE_DEACTIVATED",
                old_values=old_values,
                new_values={"is_active": False},
                created_by=current_user.id,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def list_resource_allocations(self, *, resource_id: UUID, current_user: User) -> list[ResourceAllocation]:
        resource = self.get_resource_or_404(resource_id)
        self.require_account_member(account_id=resource.account_id, user_id=current_user.id)
        return self.resources.list_allocations_for_resource(resource_id)

    def create_allocation(
        self,
        *,
        task_id: UUID,
        allocation_in: ResourceAllocationCreate,
        current_user: User,
    ) -> ResourceAllocation:
        task = self.get_task_or_404(task_id)
        self.require_account_role(
            account_id=task.account_id,
            user_id=current_user.id,
            allowed_roles=RESOURCE_ALLOCATION_WRITE_ROLES,
        )
        resource = self.validate_allocation_resource(
            resource_id=allocation_in.resource_id,
            account_id=task.account_id,
            require_active=True,
        )
        try:
            allocation = self.resources.create_allocation(
                account_id=task.account_id,
                task_id=task.id,
                resource_id=resource.id,
                allocated_hours=allocation_in.allocated_hours,
                start_date=allocation_in.start_date,
                end_date=allocation_in.end_date,
            )
            ActivityLogService(self.db).record(
                account_id=allocation.account_id,
                entity_type="RESOURCE_ALLOCATION",
                entity_id=allocation.id,
                action="RESOURCE_ALLOCATION_CREATED",
                new_values=self.allocation_activity_values(allocation),
                created_by=current_user.id,
            )
            self.db.commit()
            self.db.refresh(allocation)
            return allocation
        except Exception:
            self.db.rollback()
            raise

    def update_allocation(
        self,
        *,
        allocation_id: UUID,
        allocation_in: ResourceAllocationUpdate,
        current_user: User,
    ) -> ResourceAllocation:
        allocation = self.get_allocation_or_404(allocation_id)
        self.require_account_role(
            account_id=allocation.account_id,
            user_id=current_user.id,
            allowed_roles=RESOURCE_ALLOCATION_WRITE_ROLES,
        )
        changes = allocation_in.model_dump(exclude_unset=True)
        if "resource_id" in changes:
            resource = self.validate_allocation_resource(
                resource_id=changes["resource_id"],  # type: ignore[arg-type]
                account_id=allocation.account_id,
                require_active=True,
            )
            changes["resource_id"] = resource.id
        self.validate_allocation_date_range(
            start_date=changes.get("start_date", allocation.start_date),  # type: ignore[arg-type]
            end_date=changes.get("end_date", allocation.end_date),  # type: ignore[arg-type]
        )
        if not changes:
            return allocation

        old_values = {field: getattr(allocation, field) for field in changes}
        try:
            allocation = self.resources.update_allocation(allocation, changes)
            ActivityLogService(self.db).record(
                account_id=allocation.account_id,
                entity_type="RESOURCE_ALLOCATION",
                entity_id=allocation.id,
                action="RESOURCE_ALLOCATION_UPDATED",
                old_values=old_values,
                new_values={field: getattr(allocation, field) for field in changes},
                created_by=current_user.id,
            )
            self.db.commit()
            self.db.refresh(allocation)
            return allocation
        except Exception:
            self.db.rollback()
            raise

    def delete_allocation(self, *, allocation_id: UUID, current_user: User) -> None:
        allocation = self.get_allocation_or_404(allocation_id)
        self.require_account_role(
            account_id=allocation.account_id,
            user_id=current_user.id,
            allowed_roles=RESOURCE_ALLOCATION_WRITE_ROLES,
        )
        try:
            ActivityLogService(self.db).record(
                account_id=allocation.account_id,
                entity_type="RESOURCE_ALLOCATION",
                entity_id=allocation.id,
                action="RESOURCE_ALLOCATION_DELETED",
                old_values=self.allocation_activity_values(allocation),
                created_by=current_user.id,
            )
            self.resources.delete_allocation(allocation)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def get_resource_calendar(
        self,
        *,
        account_id: UUID,
        start_date: date,
        end_date: date,
        current_user: User,
        resource_id: UUID | None = None,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
    ) -> dict[str, object]:
        self.validate_allocation_date_range(start_date=start_date, end_date=end_date)
        self.require_account_member(account_id=account_id, user_id=current_user.id)
        if resource_id is not None:
            self.validate_resource_belongs_to_account(resource_id=resource_id, account_id=account_id)
        if project_id is not None:
            self.validate_project_belongs_to_account(project_id=project_id, account_id=account_id)
        if program_id is not None:
            self.validate_program_belongs_to_account(program_id=program_id, account_id=account_id)

        include_empty_resources = resource_id is not None or (project_id is None and program_id is None)
        calendar_resources = (
            self.resources.list_calendar_resources(account_id=account_id, resource_id=resource_id)
            if include_empty_resources
            else []
        )
        entries = {resource.id: self.empty_calendar_entry(resource) for resource in calendar_resources}

        rows = self.resources.list_calendar_allocations(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            resource_id=resource_id,
            project_id=project_id,
            program_id=program_id,
        )
        for allocation, resource, task, project, program in rows:
            if resource.id not in entries:
                entries[resource.id] = self.empty_calendar_entry(resource)
            entry = entries[resource.id]
            entry["allocations"].append(
                {
                    "id": allocation.id,
                    "task": {
                        "id": task.id,
                        "name": task.name,
                        "start_date": task.start_date,
                        "finish_date": task.finish_date,
                    },
                    "project": {"id": project.id, "name": project.name},
                    "program": {"id": program.id, "name": program.name},
                    "allocated_hours": allocation.allocated_hours,
                    "start_date": allocation.start_date,
                    "end_date": allocation.end_date,
                }
            )
            entry["total_allocated_hours"] += allocation.allocated_hours or Decimal("0")

        for entry in entries.values():
            self.finalize_calendar_entry(entry)

        return {
            "start_date": start_date,
            "end_date": end_date,
            "resources": list(entries.values()),
        }

    def empty_calendar_entry(self, resource: Resource) -> dict[str, object]:
        return {
            "resource": {
                "id": resource.id,
                "name": resource.name,
                "role": resource.role,
                "weekly_capacity_hours": resource.weekly_capacity_hours,
            },
            "allocations": [],
            "total_allocated_hours": Decimal("0"),
            "weekly_capacity_hours": resource.weekly_capacity_hours,
            "utilization_percent": 0.0,
            "overallocated": False,
        }

    def finalize_calendar_entry(self, entry: dict[str, object]) -> None:
        total = entry["total_allocated_hours"]
        capacity = entry["weekly_capacity_hours"]
        if not isinstance(total, Decimal) or not isinstance(capacity, Decimal) or capacity <= 0:
            entry["utilization_percent"] = 0.0
            entry["overallocated"] = False
            return
        # TODO: Normalize utilization by the requested date range when multi-week capacity planning is added.
        utilization = (total / capacity * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        entry["utilization_percent"] = float(utilization)
        entry["overallocated"] = total > capacity

    def validate_user_belongs_to_account(self, *, account_id: UUID, user_id: UUID | None) -> None:
        if user_id is None:
            return
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User must belong to the account.")

    def validate_resource_belongs_to_account(self, *, resource_id: UUID, account_id: UUID) -> Resource:
        resource = self.get_resource_or_404(resource_id)
        if resource.account_id != account_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Resource must belong to the account.")
        return resource

    def validate_allocation_resource(self, *, resource_id: UUID, account_id: UUID, require_active: bool) -> Resource:
        resource = self.validate_resource_belongs_to_account(resource_id=resource_id, account_id=account_id)
        if require_active and not resource.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive resources cannot receive allocations.")
        return resource

    def validate_project_belongs_to_account(self, *, project_id: UUID, account_id: UUID) -> Project:
        project = self.hierarchy.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
        if project.account_id != account_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project must belong to the account.")
        return project

    def validate_program_belongs_to_account(self, *, program_id: UUID, account_id: UUID) -> Program:
        program = self.hierarchy.get_program(program_id)
        if program is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Program not found.")
        if program.account_id != account_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Program must belong to the account.")
        return program

    def validate_allocation_date_range(self, *, start_date: date | None, end_date: date | None) -> None:
        if start_date is not None and end_date is not None and end_date < start_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_date cannot be before start_date.")

    def get_resource_or_404(self, resource_id: UUID) -> Resource:
        resource = self.resources.get_resource(resource_id)
        if resource is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
        return resource

    def get_allocation_or_404(self, allocation_id: UUID) -> ResourceAllocation:
        allocation = self.resources.get_allocation(allocation_id)
        if allocation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource allocation not found.")
        return allocation

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

    def require_account_role(self, *, account_id: UUID, user_id: UUID, allowed_roles: set[str]) -> None:
        self.require_account_member(account_id=account_id, user_id=user_id)
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None or membership.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")

    def resource_activity_values(self, resource: Resource) -> dict[str, object]:
        return {
            "id": resource.id,
            "account_id": resource.account_id,
            "user_id": resource.user_id,
            "name": resource.name,
            "role": resource.role,
            "weekly_capacity_hours": resource.weekly_capacity_hours,
            "is_active": resource.is_active,
        }

    def allocation_activity_values(self, allocation: ResourceAllocation) -> dict[str, object]:
        return {
            "id": allocation.id,
            "account_id": allocation.account_id,
            "task_id": allocation.task_id,
            "resource_id": allocation.resource_id,
            "allocated_hours": allocation.allocated_hours,
            "start_date": allocation.start_date,
            "end_date": allocation.end_date,
        }