from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.account_member import AccountMemberRole
from app.models.account_settings import DEFAULT_NON_WORKING_WEEKDAYS
from app.models.program import Program
from app.models.project import Project
from app.models.resource import Resource
from app.models.resource_allocation import ResourceAllocation
from app.models.resource_time_off import ResourceTimeOff
from app.models.task import Task
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.account_settings import AccountHolidayRepository, AccountSettingsRepository
from app.repositories.accounts import AccountRepository
from app.repositories.hierarchy import HierarchyRepository
from app.repositories.resources import ResourceRepository
from app.repositories.tasks import TaskRepository
from app.schemas.resources import (
    ResourceAllocationCreate,
    ResourceAllocationUpdate,
    ResourceCreate,
    ResourceTimeOffCreate,
    ResourceTimeOffUpdate,
    ResourceUpdate,
)
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

RESOURCE_TIME_OFF_WRITE_ROLES = RESOURCE_WRITE_ROLES
HOURS_QUANTIZER = Decimal("0.01")

WEEKDAY_INDEXES = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}


class ResourceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.account_settings = AccountSettingsRepository(db)
        self.account_holidays = AccountHolidayRepository(db)
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

    def list_resource_time_off(self, *, resource_id: UUID, current_user: User) -> list[ResourceTimeOff]:
        resource = self.get_resource_or_404(resource_id)
        self.require_account_member(account_id=resource.account_id, user_id=current_user.id)
        return self.resources.list_time_off_for_resource(resource_id)

    def create_time_off(
        self,
        *,
        resource_id: UUID,
        time_off_in: ResourceTimeOffCreate,
        current_user: User,
    ) -> ResourceTimeOff:
        resource = self.get_resource_or_404(resource_id)
        self.require_account_role(
            account_id=resource.account_id,
            user_id=current_user.id,
            allowed_roles=RESOURCE_TIME_OFF_WRITE_ROLES,
        )
        try:
            time_off = self.resources.create_time_off(
                account_id=resource.account_id,
                resource_id=resource.id,
                start_date=time_off_in.start_date,
                end_date=time_off_in.end_date,
                reason=time_off_in.reason,
                hours_per_day=time_off_in.hours_per_day,
                created_by=current_user.id,
            )
            ActivityLogService(self.db).record(
                account_id=time_off.account_id,
                entity_type="RESOURCE_TIME_OFF",
                entity_id=time_off.id,
                action="RESOURCE_TIME_OFF_CREATED",
                new_values=self.time_off_activity_values(time_off),
                created_by=current_user.id,
            )
            self.db.commit()
            self.db.refresh(time_off)
            return time_off
        except Exception:
            self.db.rollback()
            raise

    def update_time_off(
        self,
        *,
        time_off_id: UUID,
        time_off_in: ResourceTimeOffUpdate,
        current_user: User,
    ) -> ResourceTimeOff:
        time_off = self.get_time_off_or_404(time_off_id)
        self.require_account_role(
            account_id=time_off.account_id,
            user_id=current_user.id,
            allowed_roles=RESOURCE_TIME_OFF_WRITE_ROLES,
        )
        changes = time_off_in.model_dump(exclude_unset=True)
        self.validate_time_off_date_range(
            start_date=changes.get("start_date", time_off.start_date),  # type: ignore[arg-type]
            end_date=changes.get("end_date", time_off.end_date),  # type: ignore[arg-type]
        )
        if not changes:
            return time_off

        old_values = {field: getattr(time_off, field) for field in changes}
        try:
            time_off = self.resources.update_time_off(time_off, changes)
            ActivityLogService(self.db).record(
                account_id=time_off.account_id,
                entity_type="RESOURCE_TIME_OFF",
                entity_id=time_off.id,
                action="RESOURCE_TIME_OFF_UPDATED",
                old_values=old_values,
                new_values={field: getattr(time_off, field) for field in changes},
                created_by=current_user.id,
            )
            self.db.commit()
            self.db.refresh(time_off)
            return time_off
        except Exception:
            self.db.rollback()
            raise

    def delete_time_off(self, *, time_off_id: UUID, current_user: User) -> None:
        time_off = self.get_time_off_or_404(time_off_id)
        self.require_account_role(
            account_id=time_off.account_id,
            user_id=current_user.id,
            allowed_roles=RESOURCE_TIME_OFF_WRITE_ROLES,
        )
        try:
            ActivityLogService(self.db).record(
                account_id=time_off.account_id,
                entity_type="RESOURCE_TIME_OFF",
                entity_id=time_off.id,
                action="RESOURCE_TIME_OFF_DELETED",
                old_values=self.time_off_activity_values(time_off),
                created_by=current_user.id,
            )
            self.resources.delete_time_off(time_off)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

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

        non_working_weekdays, holiday_dates = self.working_calendar_for_account(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
        )
        include_empty_resources = resource_id is not None or (project_id is None and program_id is None)
        calendar_resources = (
            self.resources.list_calendar_resources(account_id=account_id, resource_id=resource_id)
            if include_empty_resources
            else []
        )
        entries = {
            resource.id: self.empty_calendar_entry(
                resource,
                start_date=start_date,
                end_date=end_date,
                non_working_weekdays=non_working_weekdays,
                holiday_dates=holiday_dates,
            )
            for resource in calendar_resources
        }

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
                entries[resource.id] = self.empty_calendar_entry(
                    resource,
                    start_date=start_date,
                    end_date=end_date,
                    non_working_weekdays=non_working_weekdays,
                    holiday_dates=holiday_dates,
                )
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

        time_off_records = self.resources.list_calendar_time_off(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            resource_ids=entries.keys(),
        )
        for time_off in time_off_records:
            entry = entries.get(time_off.resource_id)
            if entry is None:
                continue
            weekly_capacity_hours = entry["weekly_capacity_hours"]
            if not isinstance(weekly_capacity_hours, Decimal):
                continue
            entry["time_off_hours"] += self.calculate_time_off_hours(
                time_off=time_off,
                range_start=start_date,
                range_end=end_date,
                weekly_capacity_hours=weekly_capacity_hours,
                non_working_weekdays=non_working_weekdays,
                holiday_dates=holiday_dates,
            )

        for entry in entries.values():
            self.finalize_calendar_entry(entry)

        return {
            "start_date": start_date,
            "end_date": end_date,
            "resources": list(entries.values()),
        }

    def get_resource_capacity_forecast(
        self,
        *,
        resource_id: UUID,
        start_date: date,
        end_date: date,
        current_user: User,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
    ) -> dict[str, object]:
        self.validate_allocation_date_range(start_date=start_date, end_date=end_date)
        resource = self.get_resource_or_404(resource_id)
        self.require_account_member(account_id=resource.account_id, user_id=current_user.id)
        if project_id is not None:
            self.validate_project_belongs_to_account(project_id=project_id, account_id=resource.account_id)
        if program_id is not None:
            self.validate_program_belongs_to_account(program_id=program_id, account_id=resource.account_id)

        forecast_resources = self.build_capacity_forecast_resources(
            account_id=resource.account_id,
            start_date=start_date,
            end_date=end_date,
            resources=[resource],
            resource_id=resource.id,
            project_id=project_id,
            program_id=program_id,
        )
        forecast = forecast_resources[0]
        return {
            "resource": forecast["resource"],
            "start_date": start_date,
            "end_date": end_date,
            "weeks": forecast["weeks"],
        }

    def get_account_capacity_forecast(
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

        resources = self.resources.list_calendar_resources(account_id=account_id, resource_id=resource_id)
        forecast_resources = self.build_capacity_forecast_resources(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            resources=resources,
            resource_id=resource_id,
            project_id=project_id,
            program_id=program_id,
        )
        return {
            "start_date": start_date,
            "end_date": end_date,
            "resources": forecast_resources,
            "summary": self.capacity_forecast_summary(forecast_resources),
        }

    def build_capacity_forecast_resources(
        self,
        *,
        account_id: UUID,
        start_date: date,
        end_date: date,
        resources: list[Resource],
        resource_id: UUID | None = None,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
    ) -> list[dict[str, object]]:
        resource_ids = {resource.id for resource in resources}
        week_buckets = self.split_into_week_buckets(start_date=start_date, end_date=end_date)
        non_working_weekdays, holiday_dates = self.working_calendar_for_account(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
        )
        time_off_by_resource: dict[UUID, list[ResourceTimeOff]] = {resource.id: [] for resource in resources}
        allocations_by_resource: dict[UUID, list[ResourceAllocation]] = {resource.id: [] for resource in resources}

        for time_off in self.resources.list_calendar_time_off(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            resource_ids=resource_ids,
        ):
            if time_off.resource_id in time_off_by_resource:
                time_off_by_resource[time_off.resource_id].append(time_off)

        for allocation, allocation_resource, _task, _project, _program in self.resources.list_calendar_allocations(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            resource_id=resource_id,
            project_id=project_id,
            program_id=program_id,
        ):
            if allocation_resource.id in allocations_by_resource:
                allocations_by_resource[allocation_resource.id].append(allocation)

        return [
            {
                "resource": self.resource_summary(resource),
                "weeks": [
                    self.capacity_forecast_week(
                        resource=resource,
                        week_start=week_start,
                        week_end=week_end,
                        time_off_records=time_off_by_resource.get(resource.id, []),
                        allocations=allocations_by_resource.get(resource.id, []),
                        non_working_weekdays=non_working_weekdays,
                        holiday_dates=holiday_dates,
                    )
                    for week_start, week_end in week_buckets
                ],
            }
            for resource in resources
        ]

    def capacity_forecast_week(
        self,
        *,
        resource: Resource,
        week_start: date,
        week_end: date,
        time_off_records: list[ResourceTimeOff],
        allocations: list[ResourceAllocation],
        non_working_weekdays: set[int],
        holiday_dates: set[date],
    ) -> dict[str, object]:
        base_capacity = self.calculate_base_capacity_hours(
            weekly_capacity_hours=resource.weekly_capacity_hours,
            start_date=week_start,
            end_date=week_end,
            non_working_weekdays=non_working_weekdays,
            holiday_dates=holiday_dates,
        )
        time_off_hours = sum(
            (
                self.calculate_time_off_hours(
                    time_off=time_off,
                    range_start=week_start,
                    range_end=week_end,
                    weekly_capacity_hours=resource.weekly_capacity_hours,
                    non_working_weekdays=non_working_weekdays,
                    holiday_dates=holiday_dates,
                )
                for time_off in time_off_records
            ),
            Decimal("0"),
        )
        available_hours = base_capacity - time_off_hours
        if available_hours < 0:
            available_hours = Decimal("0")
        allocated_hours = sum(
            (allocation.allocated_hours or Decimal("0") for allocation in allocations if self.allocation_overlaps_range(allocation, start_date=week_start, end_date=week_end)),
            Decimal("0"),
        )
        remaining_hours = available_hours - allocated_hours
        return {
            "week_start": week_start,
            "week_end": week_end,
            "base_capacity_hours": base_capacity,
            "time_off_hours": time_off_hours,
            "available_hours": available_hours,
            "allocated_hours": allocated_hours,
            "remaining_hours": remaining_hours,
            "utilization_percent": self.calculate_utilization_percent(allocated_hours=allocated_hours, available_hours=available_hours),
            "overallocated": allocated_hours > available_hours,
        }

    def capacity_forecast_summary(self, forecast_resources: list[dict[str, object]]) -> dict[str, object]:
        total_base_capacity = Decimal("0")
        total_time_off = Decimal("0")
        total_available = Decimal("0")
        total_allocated = Decimal("0")
        total_remaining = Decimal("0")
        overallocated_resource_count = 0

        for forecast_resource in forecast_resources:
            resource_overallocated = False
            for week in forecast_resource["weeks"]:  # type: ignore[index]
                total_base_capacity += week["base_capacity_hours"]
                total_time_off += week["time_off_hours"]
                total_available += week["available_hours"]
                total_allocated += week["allocated_hours"]
                total_remaining += week["remaining_hours"]
                resource_overallocated = resource_overallocated or bool(week["overallocated"])
            if resource_overallocated:
                overallocated_resource_count += 1

        return {
            "resource_count": len(forecast_resources),
            "overallocated_resource_count": overallocated_resource_count,
            "total_base_capacity_hours": total_base_capacity,
            "total_time_off_hours": total_time_off,
            "total_available_hours": total_available,
            "total_allocated_hours": total_allocated,
            "total_remaining_hours": total_remaining,
            "average_utilization_percent": self.calculate_utilization_percent(
                allocated_hours=total_allocated,
                available_hours=total_available,
            ),
        }

    def empty_calendar_entry(
        self,
        resource: Resource,
        *,
        start_date: date,
        end_date: date,
        non_working_weekdays: set[int] | None = None,
        holiday_dates: set[date] | None = None,
    ) -> dict[str, object]:
        base_capacity_hours = self.calculate_base_capacity_hours(
            weekly_capacity_hours=resource.weekly_capacity_hours,
            start_date=start_date,
            end_date=end_date,
            non_working_weekdays=non_working_weekdays,
            holiday_dates=holiday_dates,
        )
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
            "base_capacity_hours": base_capacity_hours,
            "time_off_hours": Decimal("0"),
            "available_hours": base_capacity_hours,
            "utilization_percent": 0.0,
            "overallocated": False,
        }

    def finalize_calendar_entry(self, entry: dict[str, object]) -> None:
        total = entry["total_allocated_hours"]
        base_capacity = entry["base_capacity_hours"]
        time_off = entry["time_off_hours"]
        if not isinstance(total, Decimal) or not isinstance(base_capacity, Decimal) or not isinstance(time_off, Decimal):
            entry["utilization_percent"] = 0.0
            entry["overallocated"] = False
            return

        available = base_capacity - time_off
        if available < 0:
            available = Decimal("0")
        entry["available_hours"] = available

        entry["utilization_percent"] = self.calculate_utilization_percent(allocated_hours=total, available_hours=available)
        entry["overallocated"] = total > available

    def resource_summary(self, resource: Resource) -> dict[str, object]:
        return {
            "id": resource.id,
            "name": resource.name,
            "role": resource.role,
            "weekly_capacity_hours": resource.weekly_capacity_hours,
        }

    def calculate_base_capacity_hours(
        self,
        *,
        weekly_capacity_hours: Decimal,
        start_date: date,
        end_date: date,
        non_working_weekdays: set[int] | None = None,
        holiday_dates: set[date] | None = None,
    ) -> Decimal:
        return self.quantize_hours(
            Decimal(
                self.working_day_count_in_range(
                    start_date=start_date,
                    end_date=end_date,
                    non_working_weekdays=non_working_weekdays,
                    holiday_dates=holiday_dates,
                )
            )
            * self.daily_capacity_hours(
                weekly_capacity_hours,
                non_working_weekdays=non_working_weekdays,
            )
        )

    def calculate_time_off_hours(
        self,
        *,
        time_off: ResourceTimeOff,
        range_start: date,
        range_end: date,
        weekly_capacity_hours: Decimal,
        non_working_weekdays: set[int] | None = None,
        holiday_dates: set[date] | None = None,
    ) -> Decimal:
        overlap_start = max(range_start, time_off.start_date)
        overlap_end = min(range_end, time_off.end_date)
        working_days = self.working_day_count_in_range(
            start_date=overlap_start,
            end_date=overlap_end,
            non_working_weekdays=non_working_weekdays,
            holiday_dates=holiday_dates,
        )
        hours_per_day = time_off.hours_per_day or self.daily_capacity_hours(
            weekly_capacity_hours,
            non_working_weekdays=non_working_weekdays,
        )
        return self.quantize_hours(Decimal(working_days) * hours_per_day)

    def calculate_utilization_percent(self, *, allocated_hours: Decimal, available_hours: Decimal) -> float:
        if available_hours > 0:
            utilization = (allocated_hours / available_hours * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        elif allocated_hours > 0:
            utilization = Decimal("100")
        else:
            utilization = Decimal("0")
        return float(utilization)

    def allocation_overlaps_range(self, allocation: ResourceAllocation, *, start_date: date, end_date: date) -> bool:
        return (allocation.start_date is None or allocation.start_date <= end_date) and (
            allocation.end_date is None or allocation.end_date >= start_date
        )

    def split_into_week_buckets(self, *, start_date: date, end_date: date) -> list[tuple[date, date]]:
        buckets: list[tuple[date, date]] = []
        current_start = start_date
        while current_start <= end_date:
            days_until_sunday = 6 - current_start.weekday()
            current_end = min(current_start + timedelta(days=days_until_sunday), end_date)
            buckets.append((current_start, current_end))
            current_start = current_end + timedelta(days=1)
        return buckets

    def daily_capacity_hours(self, weekly_capacity_hours: Decimal, *, non_working_weekdays: set[int] | None = None) -> Decimal:
        non_working_weekdays = self.normalize_non_working_weekdays(non_working_weekdays)
        working_weekday_count = 7 - len(non_working_weekdays)
        if working_weekday_count <= 0:
            return Decimal("0")
        return weekly_capacity_hours / Decimal(working_weekday_count)

    def quantize_hours(self, hours: Decimal) -> Decimal:
        return hours.quantize(HOURS_QUANTIZER, rounding=ROUND_HALF_UP)

    def working_day_count_in_range(
        self,
        *,
        start_date: date,
        end_date: date,
        non_working_weekdays: set[int] | None = None,
        holiday_dates: set[date] | None = None,
    ) -> int:
        if end_date < start_date:
            return 0
        non_working_weekdays = self.normalize_non_working_weekdays(non_working_weekdays)
        holiday_dates = holiday_dates or set()
        total_days = (end_date - start_date).days + 1
        working_day_count = 0
        for offset in range(total_days):
            current_date = start_date + timedelta(days=offset)
            if current_date.weekday() not in non_working_weekdays and current_date not in holiday_dates:
                working_day_count += 1
        return working_day_count

    def weekday_count_in_range(self, *, start_date: date, end_date: date) -> int:
        return self.working_day_count_in_range(start_date=start_date, end_date=end_date)

    def working_calendar_for_account(self, *, account_id: UUID, start_date: date, end_date: date) -> tuple[set[int], set[date]]:
        settings = self.account_settings.get_for_account(account_id)
        non_working_weekdays = self.weekday_indexes(
            settings.non_working_weekdays if settings is not None else DEFAULT_NON_WORKING_WEEKDAYS
        )
        holiday_dates = self.account_holidays.list_active_dates_for_account(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
        )
        return non_working_weekdays, holiday_dates

    def weekday_indexes(self, weekday_names: list[str]) -> set[int]:
        return {WEEKDAY_INDEXES[weekday_name] for weekday_name in weekday_names if weekday_name in WEEKDAY_INDEXES}

    def normalize_non_working_weekdays(self, non_working_weekdays: set[int] | None) -> set[int]:
        if non_working_weekdays is None:
            return self.weekday_indexes(DEFAULT_NON_WORKING_WEEKDAYS)
        return non_working_weekdays

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

    def validate_time_off_date_range(self, *, start_date: date | None, end_date: date | None) -> None:
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

    def get_time_off_or_404(self, time_off_id: UUID) -> ResourceTimeOff:
        time_off = self.resources.get_time_off(time_off_id)
        if time_off is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource time off not found.")
        return time_off

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

    def time_off_activity_values(self, time_off: ResourceTimeOff) -> dict[str, object]:
        return {
            "id": time_off.id,
            "account_id": time_off.account_id,
            "resource_id": time_off.resource_id,
            "start_date": time_off.start_date,
            "end_date": time_off.end_date,
            "reason": time_off.reason,
            "hours_per_day": time_off.hours_per_day,
        }