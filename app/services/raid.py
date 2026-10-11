import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginated_response, validate_sort
from app.models.access_grant import ProjectModule
from app.models.assumption import Assumption
from app.models.change_request import ChangeRequest
from app.models.decision import Decision
from app.models.decision_option import DecisionOption
from app.models.dependency import Dependency
from app.models.issue import Issue
from app.models.option_value import OptionValue
from app.models.project import Project
from app.models.requirement import Requirement
from app.models.risk import Risk
from app.models.sprint import Sprint
from app.models.test_case import TestCase
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.hierarchy import HierarchyRepository
from app.repositories.raid import RaidRepository
from app.schemas.raid import (
    AssumptionCreate,
    AssumptionUpdate,
    DecisionCreate,
    DecisionOptionCreate,
    DecisionOptionUpdate,
    DecisionUpdate,
    IssueCreate,
    IssueUpdate,
    RiskCreate,
    RiskUpdate,
)
from app.services.access import access_for
from app.services.activity import ActivityLogService
from app.services.notifications import NotificationService
from app.models.notification import NotificationType


def normalize_option_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").upper()



RAID_CONFIGS: dict[str, dict[str, Any]] = {
    "risk": {
        "module": ProjectModule.RISKS,
        "model": Risk,
        "label": "Risk",
        "number_field": "risk_number",
        "prefix": "RISK",
        "entity_type": "RISK",
        "options": {
            "priority_id": ("PRIORITY", "Invalid risk priority."),
            "status_id": ("STATUS", "Invalid risk status."),
        },
        "account_sort_fields": {"created_at", "updated_at", "target_resolution_date", "title"},
        "search_fields": ("title", "cause", "effect"),
        "created_by_field": "created_by",
    },
    "issue": {
        "module": ProjectModule.ISSUES,
        "model": Issue,
        "label": "Issue",
        "number_field": "issue_number",
        "prefix": "ISSUE",
        "entity_type": "ISSUE",
        "options": {
            "priority_id": ("PRIORITY", "Invalid issue priority."),
            "status_id": ("STATUS", "Invalid issue status."),
        },
        "account_sort_fields": {"created_at", "updated_at", "target_resolution_date", "title"},
        "search_fields": ("title", "description"),
        "created_by_field": "created_by",
    },
    "assumption": {
        "module": ProjectModule.ASSUMPTIONS,
        "model": Assumption,
        "label": "Assumption",
        "number_field": "assumption_number",
        "prefix": "ASS",
        "entity_type": "ASSUMPTION",
        "options": {
            "status_id": ("STATUS", "Invalid assumption status."),
        },
        "account_sort_fields": {"created_at", "updated_at", "date_entered", "description"},
        "search_fields": ("description",),
        "entered_by_field": "entered_by",
    },
    "decision": {
        "module": ProjectModule.DECISIONS,
        "model": Decision,
        "label": "Decision",
        "number_field": "decision_number",
        "prefix": "DEC",
        "entity_type": "DECISION",
        "options": {
            "priority_id": ("PRIORITY", "Invalid decision priority."),
            "status_id": ("STATUS", "Invalid decision status."),
        },
        "account_sort_fields": {"created_at", "updated_at", "proposed_date", "approved_date", "title"},
        "search_fields": ("title", "description", "impact"),
        "created_by_field": "created_by",
    },
    "dependency": {
        "module": ProjectModule.DEPENDENCIES,
        "model": Dependency,
        "label": "Dependency",
        "number_field": "dependency_number",
        "prefix": "DEP",
        "entity_type": "DEPENDENCY",
        "options": {
            "priority_id": ("PRIORITY", "Invalid dependency priority."),
            "status_id": ("STATUS", "Invalid dependency status."),
        },
        "account_sort_fields": {"created_at", "updated_at", "date_entered", "title"},
        "search_fields": ("title", "description", "impact_of_delay", "fallback_plan"),
        "created_by_field": "created_by",
    },
    "change_request": {
        "module": ProjectModule.CHANGE_REQUESTS,
        "model": ChangeRequest,
        "label": "Change request",
        "number_field": "change_request_number",
        "prefix": "CR",
        "entity_type": "CHANGE_REQUEST",
        "options": {
            "priority_id": ("PRIORITY", "Invalid change request priority."),
            "status_id": ("STATUS", "Invalid change request status."),
        },
        "account_sort_fields": {"created_at", "updated_at", "proposed_date", "title"},
        "search_fields": ("title", "description", "related_record"),
        "created_by_field": "created_by",
    },
    "requirement": {
        "module": ProjectModule.REQUIREMENTS,
        "model": Requirement,
        "label": "Requirement",
        "number_field": "requirement_number",
        "prefix": "REQ",
        "entity_type": "REQUIREMENT",
        "options": {
            "priority_id": ("PRIORITY", "Invalid requirement priority."),
            "status_id": ("STATUS", "Invalid requirement status."),
        },
        "account_sort_fields": {"created_at", "updated_at", "entered_date", "title"},
        "search_fields": ("title", "description"),
        "created_by_field": "created_by",
    },
    "test_case": {
        "module": ProjectModule.TESTING,
        "model": TestCase,
        "label": "Test case",
        "number_field": "test_case_number",
        "prefix": "TC",
        "entity_type": "TEST_CASE",
        "options": {
            "priority_id": ("PRIORITY", "Invalid test case priority."),
        },
        "account_sort_fields": {"created_at", "updated_at", "execution_date", "title"},
        "search_fields": ("title", "description", "test_steps", "expected_results"),
        "created_by_field": "created_by",
        "related_fields": {"sprint_id": (Sprint, "Sprint"), "requirement_id": (Requirement, "Requirement")},
    },
}


class RaidService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.hierarchy = HierarchyRepository(db)
        self.raid = RaidRepository(db)

    def list_risks(
        self,
        *,
        project_id: UUID,
        current_user: User,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[dict[str, object]] | dict[str, object]:
        return self.list_items(
            kind="risk",
            project_id=project_id,
            current_user=current_user,
            status_id=status_id,
            priority_id=priority_id,
            sort=sort,
            pagination=pagination,
        )

    def list_account_risks(
        self,
        *,
        account_id: UUID,
        current_user: User,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        assigned_to: UUID | None = None,
        search: str | None = None,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[dict[str, object]] | dict[str, object]:
        return self.list_account_items(
            kind="risk",
            account_id=account_id,
            current_user=current_user,
            project_id=project_id,
            program_id=program_id,
            status_id=status_id,
            priority_id=priority_id,
            assigned_to=assigned_to,
            search=search,
            sort=sort,
            pagination=pagination,
        )

    def get_risk(self, *, risk_id: UUID, current_user: User) -> dict[str, object]:
        return self.get_item(kind="risk", item_id=risk_id, current_user=current_user)

    def create_risk(self, *, project_id: UUID, risk_in: RiskCreate, current_user: User) -> dict[str, object]:
        return self.create_item(kind="risk", project_id=project_id, item_in=risk_in, current_user=current_user)

    def update_risk(self, *, risk_id: UUID, risk_in: RiskUpdate, current_user: User) -> dict[str, object]:
        return self.update_item(kind="risk", item_id=risk_id, item_in=risk_in, current_user=current_user)

    def list_issues(
        self,
        *,
        project_id: UUID,
        current_user: User,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[dict[str, object]] | dict[str, object]:
        return self.list_items(
            kind="issue",
            project_id=project_id,
            current_user=current_user,
            status_id=status_id,
            priority_id=priority_id,
            sort=sort,
            pagination=pagination,
        )

    def list_account_issues(
        self,
        *,
        account_id: UUID,
        current_user: User,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        assigned_to: UUID | None = None,
        search: str | None = None,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[dict[str, object]] | dict[str, object]:
        return self.list_account_items(
            kind="issue",
            account_id=account_id,
            current_user=current_user,
            project_id=project_id,
            program_id=program_id,
            status_id=status_id,
            priority_id=priority_id,
            assigned_to=assigned_to,
            search=search,
            sort=sort,
            pagination=pagination,
        )

    def get_issue(self, *, issue_id: UUID, current_user: User) -> dict[str, object]:
        return self.get_item(kind="issue", item_id=issue_id, current_user=current_user)

    def create_issue(self, *, project_id: UUID, issue_in: IssueCreate, current_user: User) -> dict[str, object]:
        return self.create_item(kind="issue", project_id=project_id, item_in=issue_in, current_user=current_user)

    def update_issue(self, *, issue_id: UUID, issue_in: IssueUpdate, current_user: User) -> dict[str, object]:
        return self.update_item(kind="issue", item_id=issue_id, item_in=issue_in, current_user=current_user)

    def list_assumptions(
        self,
        *,
        project_id: UUID,
        current_user: User,
        status_id: UUID | None = None,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[dict[str, object]] | dict[str, object]:
        return self.list_items(
            kind="assumption",
            project_id=project_id,
            current_user=current_user,
            status_id=status_id,
            sort=sort,
            pagination=pagination,
        )

    def list_account_assumptions(
        self,
        *,
        account_id: UUID,
        current_user: User,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
        status_id: UUID | None = None,
        search: str | None = None,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[dict[str, object]] | dict[str, object]:
        return self.list_account_items(
            kind="assumption",
            account_id=account_id,
            current_user=current_user,
            project_id=project_id,
            program_id=program_id,
            status_id=status_id,
            search=search,
            sort=sort,
            pagination=pagination,
        )

    def get_assumption(self, *, assumption_id: UUID, current_user: User) -> dict[str, object]:
        return self.get_item(kind="assumption", item_id=assumption_id, current_user=current_user)

    def create_assumption(
        self,
        *,
        project_id: UUID,
        assumption_in: AssumptionCreate,
        current_user: User,
    ) -> dict[str, object]:
        return self.create_item(
            kind="assumption",
            project_id=project_id,
            item_in=assumption_in,
            current_user=current_user,
        )

    def update_assumption(
        self,
        *,
        assumption_id: UUID,
        assumption_in: AssumptionUpdate,
        current_user: User,
    ) -> dict[str, object]:
        return self.update_item(
            kind="assumption",
            item_id=assumption_id,
            item_in=assumption_in,
            current_user=current_user,
        )

    def list_decisions(
        self,
        *,
        project_id: UUID,
        current_user: User,
        status_id: UUID | None = None,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[dict[str, object]] | dict[str, object]:
        return self.list_items(
            kind="decision",
            project_id=project_id,
            current_user=current_user,
            status_id=status_id,
            sort=sort,
            pagination=pagination,
        )

    def list_account_decisions(
        self,
        *,
        account_id: UUID,
        current_user: User,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
        status_id: UUID | None = None,
        search: str | None = None,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[dict[str, object]] | dict[str, object]:
        return self.list_account_items(
            kind="decision",
            account_id=account_id,
            current_user=current_user,
            project_id=project_id,
            program_id=program_id,
            status_id=status_id,
            search=search,
            sort=sort,
            pagination=pagination,
        )

    def get_decision(self, *, decision_id: UUID, current_user: User) -> dict[str, object]:
        return self.get_item(kind="decision", item_id=decision_id, current_user=current_user)

    def create_decision(
        self,
        *,
        project_id: UUID,
        decision_in: DecisionCreate,
        current_user: User,
    ) -> dict[str, object]:
        return self.create_item(kind="decision", project_id=project_id, item_in=decision_in, current_user=current_user)

    def update_decision(
        self,
        *,
        decision_id: UUID,
        decision_in: DecisionUpdate,
        current_user: User,
    ) -> dict[str, object]:
        return self.update_item(kind="decision", item_id=decision_id, item_in=decision_in, current_user=current_user)

    def list_decision_options(self, *, decision_id: UUID, current_user: User) -> list[DecisionOption]:
        decision = self.get_decision_or_404(decision_id)
        self.require_decision_access(decision, current_user)
        return self.raid.list_decision_options(decision.id)

    def create_decision_option(
        self,
        *,
        decision_id: UUID,
        option_in: DecisionOptionCreate,
        current_user: User,
    ) -> DecisionOption:
        decision = self.get_decision_or_404(decision_id)
        self.require_decision_access(decision, current_user, edit=True)
        option = self.raid.create_decision_option(
            account_id=decision.account_id,
            decision_id=decision.id,
            title=option_in.title,
            pros=option_in.pros,
            cons=option_in.cons,
            work_effort=option_in.work_effort,
            sort_order=option_in.sort_order,
            created_by=current_user.id,
        )
        self.db.commit()
        self.db.refresh(option)
        return option

    def get_decision_option(self, *, option_id: UUID, current_user: User) -> DecisionOption:
        option = self.get_decision_option_or_404(option_id)
        self.require_decision_access(option, current_user)
        return option

    def update_decision_option(
        self,
        *,
        option_id: UUID,
        option_in: DecisionOptionUpdate,
        current_user: User,
    ) -> DecisionOption:
        option = self.get_decision_option_or_404(option_id)
        self.require_decision_access(option, current_user, edit=True)
        changes = option_in.model_dump(exclude_unset=True)
        option = self.raid.update_decision_option(option, changes)
        self.db.commit()
        self.db.refresh(option)
        return option

    def delete_decision_option(self, *, option_id: UUID, current_user: User) -> None:
        option = self.get_decision_option_or_404(option_id)
        self.require_decision_access(option, current_user, edit=True)
        self.raid.delete_decision_option(option)
        self.db.commit()

    def list_items(
        self,
        *,
        kind: str,
        project_id: UUID,
        current_user: User,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[dict[str, object]] | dict[str, object]:
        config = RAID_CONFIGS[kind]
        project = self.get_project_or_404(project_id)
        access_for(self.db, account_id=project.account_id, user=current_user).require_project(
            project, view=config["module"]
        )
        number_field = str(config["number_field"])
        allowed_sort_fields = {number_field, "created_at", "updated_at", "status_id"}
        if hasattr(config["model"], "title"):
            allowed_sort_fields.add("title")
        if hasattr(config["model"], "priority_id"):
            allowed_sort_fields.add("priority_id")
        sort_value = validate_sort(
            sort,
            allowed_fields=allowed_sort_fields,
            default=number_field,
        )
        if pagination and pagination.paginated:
            items, total = self.raid.list_items_for_project_paginated(
                config["model"],
                project_id=project.id,
                number_field=number_field,
                status_id=status_id,
                priority_id=priority_id,
                sort=sort_value,
                pagination=pagination,
            )
            return paginated_response(items=self.enrich_items(items, config), total=total, pagination=pagination)

        items = self.raid.list_items_for_project(
            config["model"],
            project_id=project.id,
            number_field=number_field,
            status_id=status_id,
            priority_id=priority_id,
            sort=sort_value,
        )
        return self.enrich_items(items, config)

    def list_account_items(
        self,
        *,
        kind: str,
        account_id: UUID,
        current_user: User,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        assigned_to: UUID | None = None,
        search: str | None = None,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[dict[str, object]] | dict[str, object]:
        config = RAID_CONFIGS[kind]
        scope = access_for(self.db, account_id=account_id, user=current_user)
        visible = scope.item_filter(config["model"].project_id, config["model"].program_id, config["module"])
        sort_value = validate_sort(
            sort,
            allowed_fields=config["account_sort_fields"],
            default="-created_at",
        )
        if project_id is not None:
            project = self.get_project_or_404(project_id)
            if project.account_id != account_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Project must belong to the account.",
                )
        if program_id is not None:
            program = self.hierarchy.get_program(program_id)
            if program is None or program.account_id != account_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Program must belong to the account.",
                )
        if pagination and pagination.paginated:
            items, total = self.raid.list_items_for_account_paginated(
                config["model"],
                account_id=account_id,
                project_id=project_id,
                program_id=program_id,
                status_id=status_id,
                priority_id=priority_id,
                assigned_to=assigned_to,
                search=search,
                search_fields=config["search_fields"],
                sort=sort_value,
                pagination=pagination,
                visible=visible,
            )
            return paginated_response(items=self.enrich_items(items, config), total=total, pagination=pagination)

        items = self.raid.list_items_for_account(
            config["model"],
            account_id=account_id,
            project_id=project_id,
            program_id=program_id,
            status_id=status_id,
            priority_id=priority_id,
            assigned_to=assigned_to,
            search=search,
            search_fields=config["search_fields"],
            sort=sort_value,
            visible=visible,
        )
        return self.enrich_items(items, config)

    def get_item(self, *, kind: str, item_id: UUID, current_user: User) -> dict[str, object]:
        item = self.get_item_or_404(kind=kind, item_id=item_id)
        self.require_item_access(item, current_user, view=RAID_CONFIGS[kind]["module"])
        return self.enrich_item(item, RAID_CONFIGS[kind])

    def create_item(
        self,
        *,
        kind: str,
        project_id: UUID,
        item_in: BaseModel,
        current_user: User,
    ) -> dict[str, object]:
        config = RAID_CONFIGS[kind]
        project = self.get_project_or_404(project_id)
        access_for(self.db, account_id=project.account_id, user=current_user).require_project(
            project, module=config["module"]
        )

        values = item_in.model_dump()
        self.resolve_option_keys(config=config, account_id=project.account_id, values=values)
        self.resolve_create_options(config=config, account_id=project.account_id, values=values)
        self.validate_references(config=config, project_id=project.id, values=values)
        if kind == "decision" and "title" in values:
            values["decision_text"] = values["title"]
        if config.get("created_by_field"):
            values[str(config["created_by_field"])] = current_user.id
        entered_by_field = config.get("entered_by_field")
        if entered_by_field and values.get(str(entered_by_field)) is None:
            values[str(entered_by_field)] = current_user.id

        values.update(
            {
                "account_id": project.account_id,
                "project_id": project.id,
                "program_id": project.program_id,
                config["number_field"]: self.generate_number(project_id=project.id, config=config),
            }
        )

        try:
            item = self.raid.create_item(config["model"], **values)
            ActivityLogService(self.db).record(
                account_id=item.account_id,
                entity_type=config["entity_type"],
                entity_id=item.id,
                action="CREATED",
                new_values=self.activity_values(item, config),
                created_by=current_user.id,
            )
            if kind == "risk" and item.assigned_to is not None:
                NotificationService(self.db).create_notification(
                    account_id=item.account_id,
                    user_id=item.assigned_to,
                    entity_type="RISK",
                    entity_id=item.id,
                    notification_type=NotificationType.RISK_CREATED,
                    title="Risk assigned to you",
                    message=f"Risk created: {item.title}",
                    actor_user_id=current_user.id,
                )
            self.db.commit()
            self.db.refresh(item)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{config['label']} number already exists for this project.",
            ) from exc
        return self.enrich_item(item, config)

    def update_item(
        self,
        *,
        kind: str,
        item_id: UUID,
        item_in: BaseModel,
        current_user: User,
    ) -> dict[str, object]:
        config = RAID_CONFIGS[kind]
        item = self.get_item_or_404(kind=kind, item_id=item_id)
        self.require_item_access(item, current_user, module=config["module"])
        changes = item_in.model_dump(exclude_unset=True)
        self.resolve_option_keys(config=config, account_id=item.account_id, values=changes)
        self.validate_update_options(config=config, account_id=item.account_id, changes=changes)
        self.validate_references(config=config, project_id=item.project_id, values=changes)
        if kind == "decision" and "title" in changes:
            changes["decision_text"] = changes["title"]
        old_values = {field: getattr(item, field) for field in changes}
        item = self.raid.update_item(item, changes)
        ActivityLogService(self.db).record(
            account_id=item.account_id,
            entity_type=config["entity_type"],
            entity_id=item.id,
            action="UPDATED",
            old_values=old_values,
            new_values={field: getattr(item, field) for field in changes},
            created_by=current_user.id,
        )
        if kind == "decision" and self.decision_changed_to_approved(old_status_id=old_values.get("status_id"), decision=item):
            NotificationService(self.db).create_for_account_members(
                account_id=item.account_id,
                entity_type="DECISION",
                entity_id=item.id,
                notification_type=NotificationType.DECISION_APPROVED,
                title="Decision approved",
                message=f"Decision approved: {item.title}",
                actor_user_id=current_user.id,
            )
        self.db.commit()
        self.db.refresh(item)
        return self.enrich_item(item, config)

    def delete_item(self, *, kind: str, item_id: UUID, current_user: User) -> None:
        config = RAID_CONFIGS[kind]
        item = self.get_item_or_404(kind=kind, item_id=item_id)
        self.require_item_access(item, current_user, module=config["module"])
        ActivityLogService(self.db).record(
            account_id=item.account_id,
            entity_type=config["entity_type"],
            entity_id=item.id,
            action="DELETED",
            old_values=self.activity_values(item, config),
            created_by=current_user.id,
        )
        self.raid.delete_item(item)
        self.db.commit()

    def resolve_option_keys(self, *, config: dict[str, Any], account_id: UUID, values: dict[str, object]) -> None:
        """Translate status/priority value keys (e.g. "in_progress") into option value ids."""
        for key, field in (("status", "status_id"), ("priority", "priority_id")):
            if key not in values:
                continue
            option_key = values.pop(key)
            if field not in config["options"] or option_key is None or values.get(field) is not None:
                continue
            option_name, detail = config["options"][field]
            option_value = self.raid.get_option_by_value(
                account_id=account_id,
                entity_type=config["entity_type"],
                option_name=option_name,
                value=normalize_option_key(str(option_key)),
            )
            if option_value is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
            values[field] = option_value.id

    def validate_references(self, *, config: dict[str, Any], project_id: UUID, values: dict[str, object]) -> None:
        for field, (model_cls, label) in config.get("related_fields", {}).items():
            related_id = values.get(field)
            if related_id is None:
                continue
            related = self.raid.get_item(model_cls, related_id)  # type: ignore[arg-type]
            if related is None or related.project_id != project_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{label} must belong to the same project.",
                )

    def activity_values(self, item: Any, config: dict[str, Any]) -> dict[str, object]:
        values: dict[str, object] = {
            "project_id": item.project_id,
            config["number_field"]: getattr(item, config["number_field"]),
        }
        for field in ("title", "description", "status_id", "priority_id"):
            if hasattr(item, field):
                values[field] = getattr(item, field)
        return values

    def resolve_create_options(self, *, config: dict[str, Any], account_id: UUID, values: dict[str, object]) -> None:
        for field, (option_name, detail) in config["options"].items():
            option_value_id = values.get(field)
            if field == "status_id" and option_value_id is None:
                values[field] = self.raid.get_default_status_id(
                    account_id=account_id,
                    entity_type=config["entity_type"],
                )
                continue
            if option_value_id is not None:
                values[field] = self.validate_option_id(
                    account_id=account_id,
                    entity_type=config["entity_type"],
                    option_name=option_name,
                    option_value_id=option_value_id,
                    detail=detail,
                )

    def validate_update_options(self, *, config: dict[str, Any], account_id: UUID, changes: dict[str, object]) -> None:
        for field, (option_name, detail) in config["options"].items():
            if field not in changes or changes[field] is None:
                continue
            changes[field] = self.validate_option_id(
                account_id=account_id,
                entity_type=config["entity_type"],
                option_name=option_name,
                option_value_id=changes[field],
                detail=detail,
            )

    def validate_option_id(
        self,
        *,
        account_id: UUID,
        entity_type: str,
        option_name: str,
        option_value_id: object,
        detail: str,
    ) -> UUID:
        option_value = self.raid.get_valid_option(
            account_id=account_id,
            entity_type=entity_type,
            option_name=option_name,
            option_value_id=option_value_id,  # type: ignore[arg-type]
        )
        if option_value is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
        return option_value.id

    def generate_number(self, *, project_id: UUID, config: dict[str, Any]) -> str:
        numbers = self.raid.list_numbers_for_project(
            config["model"],
            project_id=project_id,
            number_field=config["number_field"],
        )
        prefix = str(config["prefix"])
        highest_suffix = 0
        for number in numbers:
            if not number.startswith(f"{prefix}-"):
                continue
            suffix = number.removeprefix(f"{prefix}-")
            if suffix.isdigit():
                highest_suffix = max(highest_suffix, int(suffix))
        return f"{prefix}-{highest_suffix + 1:03d}"

    def enrich_item(self, item: Any, config: dict[str, Any]) -> dict[str, object]:
        return self.enrich_items([item], config)[0]

    def enrich_items(self, items: list[Any], config: dict[str, Any]) -> list[dict[str, object]]:
        option_fields = tuple(config["options"].keys())
        option_ids = {
            option_id
            for item in items
            for option_id in (getattr(item, option_field) for option_field in option_fields)
            if option_id is not None
        }
        options = self.raid.get_option_values_by_ids(option_ids)
        projects = self.raid.get_projects_by_ids({item.project_id for item in items})
        programs = self.raid.get_programs_by_ids(
            {item.program_id for item in items if getattr(item, "program_id", None) is not None}
        )
        author_field = config.get("created_by_field") or config.get("entered_by_field")
        authors = self.raid.get_users_by_ids(
            {getattr(item, author_field) for item in items if getattr(item, author_field, None) is not None}
        )
        comment_counts = self.raid.count_comments(
            entity_type=config["entity_type"],
            entity_ids={item.id for item in items},
        )
        linked_test_cases = (
            self.raid.get_test_cases_by_requirement({item.id for item in items})
            if config["model"] is Requirement
            else {}
        )
        requirement_numbers: dict[UUID, str] = {}
        sprint_names: dict[UUID, str] = {}
        if config["model"] is TestCase:
            requirement_numbers = self.raid.get_labels_by_ids(
                Requirement, "requirement_number", {item.requirement_id for item in items if item.requirement_id}
            )
            sprint_names = self.raid.get_labels_by_ids(
                Sprint, "name", {item.sprint_id for item in items if item.sprint_id}
            )
        enriched_items: list[dict[str, object]] = []
        for item in items:
            item_data = {**item.__dict__}
            for option_field in option_fields:
                summary_field = option_field[:-3] if option_field.endswith("_id") else option_field
                item_data[summary_field] = self.option_summary(getattr(item, option_field), options)
            project = projects.get(item.project_id)
            program = programs.get(item.program_id)
            item_data["project"] = {"id": project.id, "name": project.name} if project is not None else None
            item_data["portfolio_id"] = project.portfolio_id if project is not None else None
            item_data["program"] = {"id": program.id, "name": program.name} if program is not None else None
            author = authors.get(getattr(item, author_field, None))
            item_data["created_by_name"] = (author.full_name or author.email) if author is not None else None
            item_data["comment_count"] = comment_counts.get(item.id, 0)
            if config["model"] is Requirement:
                test_cases = linked_test_cases.get(item.id, [])
                item_data["test_case_ids"] = [test_case_id for test_case_id, _ in test_cases]
                item_data["test_case_numbers"] = [number for _, number in test_cases]
            if config["model"] is TestCase:
                item_data["requirement_number"] = requirement_numbers.get(item.requirement_id)
                item_data["sprint_name"] = sprint_names.get(item.sprint_id)
            enriched_items.append(item_data)
        return enriched_items

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

    def decision_changed_to_approved(self, *, old_status_id: object, decision: Decision) -> bool:
        if old_status_id == decision.status_id or decision.status_id is None:
            return False
        status_value = self.raid.get_option_values_by_ids([decision.status_id]).get(decision.status_id)
        return status_value is not None and status_value.value == "APPROVED"

    def get_project_or_404(self, project_id: UUID) -> Project:
        project = self.hierarchy.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
        return project

    def get_item_or_404(self, *, kind: str, item_id: UUID) -> Any:
        config = RAID_CONFIGS[kind]
        item = self.raid.get_item(config["model"], item_id)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{config['label']} not found.")
        return item

    def get_decision_or_404(self, decision_id: UUID) -> Decision:
        decision = self.raid.get_item(Decision, decision_id)
        if decision is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found.")
        return decision

    def get_decision_option_or_404(self, option_id: UUID) -> DecisionOption:
        option = self.raid.get_decision_option(option_id)
        if option is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision option not found.")
        return option

    def require_item_access(
        self,
        item: Any,
        current_user: User,
        *,
        module: ProjectModule | None = None,
        view: ProjectModule | None = None,
    ) -> None:
        """Sight of `view` (or of `module`) in the item's project or program, plus edit access to `module`."""
        access_for(self.db, account_id=item.account_id, user=current_user).require_item(
            project_id=item.project_id,
            program_id=getattr(item, "program_id", None),
            module=module,
            view=view or module,
        )

    def require_decision_access(
        self, record: Decision | DecisionOption, current_user: User, *, edit: bool = False
    ) -> None:
        decision = record if isinstance(record, Decision) else self.get_decision_or_404(record.decision_id)
        self.require_item_access(
            decision, current_user, module=ProjectModule.DECISIONS if edit else None, view=ProjectModule.DECISIONS
        )
