from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import re
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.account_member import AccountMemberRole
from app.models.account_settings import DEFAULT_NON_WORKING_WEEKDAYS
from app.models.option_value import OptionValue
from app.models.program import Program
from app.models.project import Project
from app.models.resource import Resource
from app.models.resource_allocation import ResourceAllocation
from app.models.resource_skill import ResourceSkill
from app.models.resource_time_off import ResourceTimeOff
from app.models.skill import Skill
from app.models.task import Task
from app.models.task_assignment import TaskAssignment
from app.models.task_required_skill import TaskRequiredSkill
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.account_settings import AccountHolidayRepository, AccountSettingsRepository
from app.repositories.accounts import AccountRepository
from app.repositories.hierarchy import HierarchyRepository
from app.repositories.resources import ResourceRepository
from app.repositories.skills import SkillRepository
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
HIGH_PRIORITY_KEYS = {"high", "critical"}
LIMITED_AVAILABLE_HOURS_WARNING_THRESHOLD = Decimal("8")
HIGH_UTILIZATION_WARNING_PERCENT = 80.0
DEFAULT_TASK_DEMAND_HOURS = Decimal("8")
PROFICIENCY_RANK = {
    "BEGINNER": 1,
    "INTERMEDIATE": 2,
    "ADVANCED": 3,
    "EXPERT": 4,
}

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
        self.skills = SkillRepository(db)
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

    def get_resource_analysis(
        self,
        *,
        account_id: UUID,
        start_date: date,
        end_date: date,
        current_user: User,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
        resource_id: UUID | None = None,
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
        resource_analysis = [self.resource_analysis_summary(forecast_resource) for forecast_resource in forecast_resources]
        overallocated_resources = [entry for entry in resource_analysis if entry["allocated_hours"] > entry["available_hours"]]
        underutilized_resources = [
            entry
            for entry in resource_analysis
            if entry["available_hours"] > 0 and float(entry["utilization_percent"]) < 50
        ]

        task_contexts = self.resource_analysis_task_contexts(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            project_id=project_id,
            program_id=program_id,
            resource_id=resource_id,
        )
        unassigned_tasks = self.resource_analysis_unassigned_tasks(task_contexts)
        critical_unstaffed_tasks = self.resource_analysis_critical_unstaffed_tasks(task_contexts)
        skill_gaps = self.resource_analysis_skill_gaps(task_contexts)
        future_shortages = self.resource_analysis_future_shortages(
            start_date=start_date,
            end_date=end_date,
            forecast_resources=forecast_resources,
            task_contexts=task_contexts,
        )
        suggestions = self.resource_analysis_suggestions(
            overallocated_resources=overallocated_resources,
            underutilized_resources=underutilized_resources,
            unassigned_tasks=unassigned_tasks,
            skill_gaps=skill_gaps,
        )

        return {
            "summary": {
                "resource_count": len(resource_analysis),
                "overallocated_count": len(overallocated_resources),
                "underutilized_count": len(underutilized_resources),
                "unassigned_task_count": len(unassigned_tasks),
                "skill_gap_count": len(skill_gaps),
            },
            "overallocated_resources": overallocated_resources,
            "underutilized_resources": underutilized_resources,
            "unassigned_tasks": unassigned_tasks,
            "critical_unstaffed_tasks": critical_unstaffed_tasks,
            "skill_gaps": skill_gaps,
            "future_shortages": future_shortages,
            "suggestions": suggestions,
        }

    def get_task_resource_recommendations(self, *, task_id: UUID, current_user: User) -> list[dict[str, object]]:
        task = self.get_task_or_404(task_id)
        self.require_account_member(account_id=task.account_id, user_id=current_user.id)
        start_date = task.start_date or task.finish_date or date.today()
        end_date = task.finish_date or task.start_date or start_date
        self.validate_allocation_date_range(start_date=start_date, end_date=end_date)

        resources = self.resources.list_calendar_resources(account_id=task.account_id)
        forecast_resources = self.build_capacity_forecast_resources(
            account_id=task.account_id,
            start_date=start_date,
            end_date=end_date,
            resources=resources,
        )
        required_skills = self.skills.list_task_required_skills(task.id)
        resource_skills_by_resource = self.skills.list_resource_skills_for_resources(
            [resource.id for resource in resources]
        )
        recommendations = [
            self.resource_recommendation(
                forecast_resource=forecast_resource,
                required_skills=required_skills,
                resource_skills_by_resource=resource_skills_by_resource,
            )
            for forecast_resource in forecast_resources
        ]
        return sorted(
            recommendations,
            key=lambda recommendation: (
                -int(recommendation["score"]),
                str(recommendation["resource"]["name"]),
                str(recommendation["resource"]["id"]),
            ),
        )

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

    def resource_analysis_summary(self, forecast_resource: dict[str, object]) -> dict[str, object]:
        allocated_hours, available_hours = self.forecast_totals(forecast_resource)
        remaining_hours = available_hours - allocated_hours
        return {
            "resource": forecast_resource["resource"],
            "allocated_hours": allocated_hours,
            "available_hours": available_hours,
            "remaining_hours": remaining_hours,
            "utilization_percent": self.calculate_utilization_percent(
                allocated_hours=allocated_hours,
                available_hours=available_hours,
            ),
            "overallocated": allocated_hours > available_hours,
        }

    def resource_analysis_task_contexts(
        self,
        *,
        account_id: UUID,
        start_date: date,
        end_date: date,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
        resource_id: UUID | None = None,
    ) -> list[dict[str, object]]:
        rows = self.tasks.list_tasks_for_resource_analysis(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            project_id=project_id,
            program_id=program_id,
        )
        tasks = [task for task, _project, _program in rows]
        task_ids = [task.id for task in tasks]
        assignments_by_task = self.tasks.list_assignments_for_tasks(task_ids)
        allocations_by_task = self.tasks.list_resource_allocations_for_tasks(task_ids)
        required_skills_by_task = self.skills.list_task_required_skills_for_tasks(task_ids)
        priority_values = self.tasks.get_option_values_by_ids(
            {task.priority_id for task in tasks if task.priority_id is not None}
        )
        user_ids = {
            assignment.user_id
            for assignments in assignments_by_task.values()
            for assignment in assignments
            if assignment.user_id is not None
        }
        resources_by_user_id: dict[UUID, list[Resource]] = {}
        for resource in self.resources.list_resources_for_account_by_user_ids(account_id=account_id, user_ids=user_ids):
            resources_by_user_id.setdefault(resource.user_id, []).append(resource)  # type: ignore[arg-type]

        contexts: list[dict[str, object]] = []
        for task, project, program in rows:
            assignments = assignments_by_task.get(task.id, [])
            allocations = [
                (allocation, resource)
                for allocation, resource in allocations_by_task.get(task.id, [])
                if self.allocation_overlaps_range(allocation, start_date=start_date, end_date=end_date)
            ]
            assigned_resources = self.task_assigned_resources(
                assignments=assignments,
                allocations=allocations,
                resources_by_user_id=resources_by_user_id,
            )
            if resource_id is not None and all(resource.id != resource_id for resource in assigned_resources):
                continue
            contexts.append(
                {
                    "task": task,
                    "project": project,
                    "program": program,
                    "assignments": assignments,
                    "all_allocations": allocations_by_task.get(task.id, []),
                    "allocations": allocations,
                    "assigned_resources": assigned_resources,
                    "required_skills": required_skills_by_task.get(task.id, []),
                    "priority": priority_values.get(task.priority_id) if task.priority_id is not None else None,
                }
            )
        return contexts

    def task_assigned_resources(
        self,
        *,
        assignments: list[TaskAssignment],
        allocations: list[tuple[ResourceAllocation, Resource]],
        resources_by_user_id: dict[UUID, list[Resource]],
    ) -> list[Resource]:
        assigned_resources: list[Resource] = []
        seen_resource_ids: set[UUID] = set()
        for _allocation, resource in allocations:
            if resource.id in seen_resource_ids:
                continue
            seen_resource_ids.add(resource.id)
            assigned_resources.append(resource)
        for assignment in assignments:
            if assignment.user_id is None:
                continue
            for resource in resources_by_user_id.get(assignment.user_id, []):
                if resource.id in seen_resource_ids:
                    continue
                seen_resource_ids.add(resource.id)
                assigned_resources.append(resource)
        return assigned_resources

    def resource_analysis_unassigned_tasks(self, task_contexts: list[dict[str, object]]) -> list[dict[str, object]]:
        unassigned_tasks: list[dict[str, object]] = []
        for context in task_contexts:
            assignments = context["assignments"]
            allocations = context["allocations"]
            if assignments or allocations:
                continue
            task = context["task"]
            project = context["project"]
            program = context["program"]
            if not isinstance(task, Task) or not isinstance(project, Project) or not isinstance(program, Program):
                continue
            unassigned_tasks.append(
                {
                    "task": self.resource_analysis_task_summary(
                        task=task,
                        project=project,
                        program=program,
                        priority_value=context.get("priority") if isinstance(context.get("priority"), OptionValue) else None,
                    ),
                    "reasons": ["Task has no assignment or resource allocation in the date range."],
                }
            )
        return unassigned_tasks

    def resource_analysis_skill_gaps(self, task_contexts: list[dict[str, object]]) -> list[dict[str, object]]:
        gaps: list[dict[str, object]] = []
        all_assigned_resource_ids = {
            resource.id
            for context in task_contexts
            for resource in context["assigned_resources"]
            if isinstance(resource, Resource)
        }
        skills_by_resource = self.skills.list_resource_skills_for_resources(all_assigned_resource_ids)
        for context in task_contexts:
            task = context["task"]
            project = context["project"]
            program = context["program"]
            if not isinstance(task, Task) or not isinstance(project, Project) or not isinstance(program, Program):
                continue
            assigned_resources = [
                resource for resource in context["assigned_resources"] if isinstance(resource, Resource)
            ]
            missing_skill_entries: list[tuple[TaskRequiredSkill, Skill]] = []
            for required_skill, skill in context["required_skills"]:
                if self.any_resource_matches_required_skill(
                    assigned_resources=assigned_resources,
                    required_skill=required_skill,
                    skills_by_resource=skills_by_resource,
                ):
                    continue
                missing_skill_entries.append((required_skill, skill))
            missing_skill_names = [skill.name for _required_skill, skill in missing_skill_entries]
            for required_skill, skill in missing_skill_entries:
                gaps.append(
                    {
                        "task": self.resource_analysis_task_summary(
                            task=task,
                            project=project,
                            program=program,
                            priority_value=context.get("priority") if isinstance(context.get("priority"), OptionValue) else None,
                        ),
                        "skill": self.resource_analysis_skill_summary(required_skill=required_skill, skill=skill),
                        "missing_skills": missing_skill_names,
                        "assigned_resources": [self.resource_summary(resource) for resource in assigned_resources],
                        "message": (
                            f"Missing required skills: {', '.join(missing_skill_names)}."
                            if len(missing_skill_names) > 1
                            else f"No assigned or allocated resource matches required skill: {skill.name}."
                        ),
                    }
                )
        return gaps

    def resource_analysis_critical_unstaffed_tasks(self, task_contexts: list[dict[str, object]]) -> list[dict[str, object]]:
        critical_tasks: list[dict[str, object]] = []
        for context in task_contexts:
            assignments = context["assignments"]
            all_allocations = context.get("all_allocations", context["allocations"])
            if assignments or all_allocations:
                continue
            priority_value = context.get("priority")
            if not isinstance(priority_value, OptionValue) or not self.is_high_or_critical_priority(priority_value):
                continue
            task = context["task"]
            project = context["project"]
            program = context["program"]
            if not isinstance(task, Task) or not isinstance(project, Project) or not isinstance(program, Program):
                continue
            critical_tasks.append(
                {
                    "task": self.resource_analysis_task_summary(
                        task=task,
                        project=project,
                        program=program,
                        priority_value=priority_value,
                    ),
                    "reasons": ["High or critical priority task has no assignment or resource allocation in the date range."],
                }
            )
        return critical_tasks

    def resource_analysis_future_shortages(
        self,
        *,
        start_date: date,
        end_date: date,
        forecast_resources: list[dict[str, object]],
        task_contexts: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        week_buckets = self.split_into_week_buckets(start_date=start_date, end_date=end_date)
        resource_ids = [
            forecast_resource["resource"]["id"]  # type: ignore[index]
            for forecast_resource in forecast_resources
            if isinstance(forecast_resource.get("resource"), dict)
            and isinstance(forecast_resource["resource"].get("id"), UUID)  # type: ignore[index, union-attr]
        ]
        resource_skills_by_resource = self.skills.list_resource_skills_for_resources(resource_ids)

        skill_names: dict[UUID, str] = {}
        available_by_skill_period: dict[tuple[UUID, date, date], Decimal] = {}
        for forecast_resource in forecast_resources:
            resource_summary = forecast_resource.get("resource")
            if not isinstance(resource_summary, dict):
                continue
            resource_id = resource_summary.get("id")
            if not isinstance(resource_id, UUID):
                continue
            resource_skills = resource_skills_by_resource.get(resource_id, [])
            if not resource_skills:
                continue
            for week in forecast_resource["weeks"]:  # type: ignore[index]
                remaining_hours = week["remaining_hours"]
                remaining = remaining_hours if isinstance(remaining_hours, Decimal) else Decimal(str(remaining_hours or 0))
                if remaining <= 0:
                    continue
                for _resource_skill, skill in resource_skills:
                    skill_names[skill.id] = skill.name
                    key = (skill.id, week["week_start"], week["week_end"])
                    available_by_skill_period[key] = available_by_skill_period.get(key, Decimal("0")) + remaining

        required_by_skill_period: dict[tuple[UUID, date, date], Decimal] = {}
        for context in task_contexts:
            task = context["task"]
            if not isinstance(task, Task):
                continue
            for required_skill, skill in context["required_skills"]:
                skill_names[skill.id] = skill.name
                for period_start, period_end in week_buckets:
                    if not self.task_overlaps_period(task=task, period_start=period_start, period_end=period_end):
                        continue
                    required_hours = self.resource_analysis_required_hours_for_period(
                        task=task,
                        period_start=period_start,
                        period_end=period_end,
                    )
                    if required_hours <= 0:
                        continue
                    key = (required_skill.skill_id, period_start, period_end)
                    required_by_skill_period[key] = required_by_skill_period.get(key, Decimal("0")) + required_hours

        shortages: list[dict[str, object]] = []
        for key, required_hours in required_by_skill_period.items():
            skill_id, period_start, period_end = key
            available_hours = available_by_skill_period.get(key, Decimal("0"))
            if required_hours <= available_hours:
                continue
            shortage_hours = required_hours - available_hours
            shortages.append(
                {
                    "skill": skill_names.get(skill_id, str(skill_id)),
                    "period_start": period_start,
                    "period_end": period_end,
                    "required_hours": self.quantize_hours(required_hours),
                    "available_hours": self.quantize_hours(available_hours),
                    "shortage_hours": self.quantize_hours(shortage_hours),
                }
            )
        return sorted(shortages, key=lambda entry: (entry["period_start"], entry["skill"]))

    def task_overlaps_period(self, *, task: Task, period_start: date, period_end: date) -> bool:
        return (task.start_date is None or task.start_date <= period_end) and (
            task.finish_date is None or task.finish_date >= period_start
        )

    def resource_analysis_required_hours_for_period(
        self,
        *,
        task: Task,
        period_start: date,
        period_end: date,
    ) -> Decimal:
        if task.start_date is None and task.finish_date is None:
            return DEFAULT_TASK_DEMAND_HOURS
        task_start = task.start_date or task.finish_date or period_start
        task_end = task.finish_date or task.start_date or period_end
        overlap_start = max(task_start, period_start)
        overlap_end = min(task_end, period_end)
        working_days = self.working_day_count_in_range(start_date=overlap_start, end_date=overlap_end)
        if working_days <= 0:
            return Decimal("0")
        if task.duration_days is not None and task.duration_days > 0:
            planned_days = min(Decimal(working_days), task.duration_days)
        else:
            planned_days = Decimal(working_days)
        return self.quantize_hours(planned_days * DEFAULT_TASK_DEMAND_HOURS)

    def resource_analysis_suggestions(
        self,
        *,
        overallocated_resources: list[dict[str, object]],
        underutilized_resources: list[dict[str, object]],
        unassigned_tasks: list[dict[str, object]],
        skill_gaps: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        suggestions: list[dict[str, object]] = []
        for entry in overallocated_resources:
            resource = entry["resource"]
            suggestions.append(
                {
                    "type": "OVERALLOCATION",
                    "message": "Review workload or move work to an available resource; no changes were applied.",
                    "resource": resource,
                    "task": None,
                    "skill": None,
                }
            )
        for entry in underutilized_resources:
            suggestions.append(
                {
                    "type": "UNDERUTILIZATION",
                    "message": "Consider this resource for additional work if their skills match demand.",
                    "resource": entry["resource"],
                    "task": None,
                    "skill": None,
                }
            )
        for entry in unassigned_tasks:
            suggestions.append(
                {
                    "type": "UNASSIGNED_TASK",
                    "message": "Review resource recommendations and assign a suitable resource manually.",
                    "resource": None,
                    "task": entry["task"],
                    "skill": None,
                }
            )
        for entry in skill_gaps:
            suggestions.append(
                {
                    "type": "SKILL_GAP",
                    "message": "Find a matching skilled resource or adjust the required skill manually.",
                    "resource": None,
                    "task": entry["task"],
                    "skill": entry["skill"],
                }
            )
        return suggestions

    def resource_recommendation(
        self,
        *,
        forecast_resource: dict[str, object],
        required_skills: list[tuple[TaskRequiredSkill, Skill]],
        resource_skills_by_resource: dict[UUID, list[tuple[ResourceSkill, Skill]]],
    ) -> dict[str, object]:
        resource_summary = forecast_resource["resource"]
        resource_id = resource_summary["id"]  # type: ignore[index]
        allocated_hours, available_hours = self.forecast_totals(forecast_resource)
        time_off_hours = self.forecast_time_off_total(forecast_resource)
        remaining_hours = available_hours - allocated_hours
        utilization_percent = self.calculate_utilization_percent(
            allocated_hours=allocated_hours,
            available_hours=available_hours,
        )
        resource_skills = resource_skills_by_resource.get(resource_id, [])

        reasons: list[str] = []
        warnings: list[str] = []
        skill_score, skill_reasons, skill_warnings = self.recommendation_skill_score(
            required_skills=required_skills,
            resource_skills=resource_skills,
        )
        reasons.extend(skill_reasons)
        warnings.extend(skill_warnings)

        if remaining_hours <= LIMITED_AVAILABLE_HOURS_WARNING_THRESHOLD:
            warnings.append("Resource has limited available hours")
        if remaining_hours > 0:
            availability_score = 30
            reasons.append(f"Has {self.format_hours(remaining_hours)} available hours")
        else:
            availability_score = 0
            warnings.append("No available hours in the task date range")

        utilization_score = self.recommendation_utilization_score(utilization_percent)
        reasons.append(f"Currently {self.format_percent(utilization_percent)} utilized")
        if utilization_percent >= HIGH_UTILIZATION_WARNING_PERCENT:
            warnings.append("Resource already highly utilized")
        if allocated_hours > available_hours:
            warnings.append("Currently overallocated")
        if time_off_hours > 0:
            warnings.append("Resource has time off during the task window")

        score = min(100, skill_score + availability_score + utilization_score)

        return {
            "resource": resource_summary,
            "score": score,
            "confidence": self.recommendation_confidence(score),
            "reasons": reasons,
            "warnings": warnings,
        }

    def recommendation_skill_score(
        self,
        *,
        required_skills: list[tuple[TaskRequiredSkill, Skill]],
        resource_skills: list[tuple[ResourceSkill, Skill]],
    ) -> tuple[int, list[str], list[str]]:
        if not required_skills:
            return 50, ["No required skills specified"], []

        matched_count = 0
        reasons: list[str] = []
        warnings: list[str] = []
        for required_skill, skill in required_skills:
            matching_resource_skill = self.matching_resource_skill(
                required_skill=required_skill,
                resource_skills=resource_skills,
            )
            if matching_resource_skill is None:
                warnings.append(f"Missing required skill: {skill.name}")
                continue
            matched_count += 1
            reasons.append(f"Matches required skill: {skill.name}")
        if 0 < matched_count < len(required_skills):
            warnings.append("Resource only partially matches required skills")
        return round(50 * matched_count / len(required_skills)), reasons, warnings

    def recommendation_confidence(self, score: int) -> str:
        if score >= 90:
            return "HIGH"
        if score >= 70:
            return "MEDIUM"
        return "LOW"

    def matching_resource_skill(
        self,
        *,
        required_skill: TaskRequiredSkill,
        resource_skills: list[tuple[ResourceSkill, Skill]],
    ) -> ResourceSkill | None:
        for resource_skill, _skill in resource_skills:
            if self.resource_skill_matches_required_skill(resource_skill, required_skill):
                return resource_skill
        return None

    def recommendation_utilization_score(self, utilization_percent: float) -> int:
        if utilization_percent < 50:
            return 20
        if utilization_percent <= 80:
            return 15
        if utilization_percent <= 100:
            return 5
        return 0

    def forecast_totals(self, forecast_resource: dict[str, object]) -> tuple[Decimal, Decimal]:
        allocated_hours = Decimal("0")
        available_hours = Decimal("0")
        for week in forecast_resource["weeks"]:  # type: ignore[index]
            allocated_hours += week["allocated_hours"]
            available_hours += week["available_hours"]
        return allocated_hours, available_hours

    def forecast_time_off_total(self, forecast_resource: dict[str, object]) -> Decimal:
        time_off_hours = Decimal("0")
        for week in forecast_resource["weeks"]:  # type: ignore[index]
            week_time_off = week["time_off_hours"]
            time_off_hours += week_time_off if isinstance(week_time_off, Decimal) else Decimal(str(week_time_off or 0))
        return time_off_hours

    def resource_analysis_task_summary(
        self,
        *,
        task: Task,
        project: Project,
        program: Program,
        priority_value: OptionValue | None = None,
    ) -> dict[str, object]:
        return {
            "id": task.id,
            "project_id": task.project_id,
            "name": task.name,
            "start_date": task.start_date,
            "finish_date": task.finish_date,
            "priority": self.option_summary(priority_value),
            "project": {"id": project.id, "name": project.name},
            "program": {"id": program.id, "name": program.name},
        }

    def resource_analysis_skill_summary(self, *, required_skill: TaskRequiredSkill, skill: Skill) -> dict[str, object]:
        return {
            "id": skill.id,
            "name": skill.name,
            "category": skill.category,
            "required_proficiency": required_skill.required_proficiency,
        }

    def any_resource_matches_required_skill(
        self,
        *,
        assigned_resources: list[Resource],
        required_skill: TaskRequiredSkill,
        skills_by_resource: dict[UUID, list[tuple[ResourceSkill, Skill]]],
    ) -> bool:
        return any(
            self.matching_resource_skill(
                required_skill=required_skill,
                resource_skills=skills_by_resource.get(resource.id, []),
            )
            is not None
            for resource in assigned_resources
        )

    def resource_skill_matches_required_skill(self, resource_skill: ResourceSkill, required_skill: TaskRequiredSkill) -> bool:
        if resource_skill.skill_id != required_skill.skill_id:
            return False
        if required_skill.required_proficiency is None:
            return True
        return PROFICIENCY_RANK.get(resource_skill.proficiency, 0) >= PROFICIENCY_RANK.get(
            required_skill.required_proficiency,
            0,
        )

    def option_summary(self, option_value: OptionValue | None) -> dict[str, object] | None:
        if option_value is None:
            return None
        return {
            "id": option_value.id,
            "label": option_value.label,
            "value": option_value.value,
            "color": option_value.color,
        }

    def is_high_or_critical_priority(self, option_value: OptionValue | None) -> bool:
        if option_value is None:
            return False
        return bool(
            {
                self.normalize_option_key(option_value.value),
                self.normalize_option_key(option_value.label),
            }
            & HIGH_PRIORITY_KEYS
        )

    def normalize_option_key(self, value: str | None) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())

    def format_hours(self, hours: Decimal) -> str:
        quantized = self.quantize_hours(hours)
        return f"{quantized.normalize():f}"

    def format_percent(self, utilization_percent: float) -> str:
        if utilization_percent.is_integer():
            return f"{int(utilization_percent)}%"
        return f"{utilization_percent:.2f}%"

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