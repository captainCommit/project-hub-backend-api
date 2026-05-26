from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginated_response, validate_sort
from app.models.account_member import AccountMemberRole
from app.models.assumption import Assumption
from app.models.decision import Decision
from app.models.decision_option import DecisionOption
from app.models.issue import Issue
from app.models.option_value import OptionValue
from app.models.project import Project
from app.models.risk import Risk
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
from app.services.activity import ActivityLogService
from app.services.notifications import NotificationService
from app.models.notification import NotificationType


RAID_WRITE_ROLES = {
    AccountMemberRole.OWNER.value,
    AccountMemberRole.ADMIN.value,
    AccountMemberRole.MANAGER.value,
    AccountMemberRole.MEMBER.value,
}

RAID_CONFIGS: dict[str, dict[str, Any]] = {
    "risk": {
        "model": Risk,
        "label": "Risk",
        "number_field": "risk_number",
        "prefix": "RISK",
        "entity_type": "RISK",
        "options": {
            "priority_id": ("PRIORITY", "Invalid risk priority."),
            "status_id": ("STATUS", "Invalid risk status."),
        },
        "created_by_field": "created_by",
    },
    "issue": {
        "model": Issue,
        "label": "Issue",
        "number_field": "issue_number",
        "prefix": "ISSUE",
        "entity_type": "ISSUE",
        "options": {
            "priority_id": ("PRIORITY", "Invalid issue priority."),
            "status_id": ("STATUS", "Invalid issue status."),
        },
        "created_by_field": "created_by",
    },
    "assumption": {
        "model": Assumption,
        "label": "Assumption",
        "number_field": "assumption_number",
        "prefix": "ASS",
        "entity_type": "ASSUMPTION",
        "options": {
            "status_id": ("STATUS", "Invalid assumption status."),
        },
        "entered_by_field": "entered_by",
    },
    "decision": {
        "model": Decision,
        "label": "Decision",
        "number_field": "decision_number",
        "prefix": "DEC",
        "entity_type": "DECISION",
        "options": {
            "status_id": ("STATUS", "Invalid decision status."),
        },
        "created_by_field": "created_by",
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
        self.require_account_member(account_id=decision.account_id, user_id=current_user.id)
        return self.raid.list_decision_options(decision.id)

    def create_decision_option(
        self,
        *,
        decision_id: UUID,
        option_in: DecisionOptionCreate,
        current_user: User,
    ) -> DecisionOption:
        decision = self.get_decision_or_404(decision_id)
        self.require_account_role(
            account_id=decision.account_id,
            user_id=current_user.id,
            allowed_roles=RAID_WRITE_ROLES,
        )
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
        self.require_account_member(account_id=option.account_id, user_id=current_user.id)
        return option

    def update_decision_option(
        self,
        *,
        option_id: UUID,
        option_in: DecisionOptionUpdate,
        current_user: User,
    ) -> DecisionOption:
        option = self.get_decision_option_or_404(option_id)
        self.require_account_role(
            account_id=option.account_id,
            user_id=current_user.id,
            allowed_roles=RAID_WRITE_ROLES,
        )
        changes = option_in.model_dump(exclude_unset=True)
        option = self.raid.update_decision_option(option, changes)
        self.db.commit()
        self.db.refresh(option)
        return option

    def delete_decision_option(self, *, option_id: UUID, current_user: User) -> None:
        option = self.get_decision_option_or_404(option_id)
        self.require_account_role(
            account_id=option.account_id,
            user_id=current_user.id,
            allowed_roles=RAID_WRITE_ROLES,
        )
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
        self.require_account_member(account_id=project.account_id, user_id=current_user.id)
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

    def get_item(self, *, kind: str, item_id: UUID, current_user: User) -> dict[str, object]:
        item = self.get_item_or_404(kind=kind, item_id=item_id)
        self.require_account_member(account_id=item.account_id, user_id=current_user.id)
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
        self.require_account_role(
            account_id=project.account_id,
            user_id=current_user.id,
            allowed_roles=RAID_WRITE_ROLES,
        )

        values = item_in.model_dump()
        self.resolve_create_options(config=config, account_id=project.account_id, values=values)
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
        self.require_account_role(
            account_id=item.account_id,
            user_id=current_user.id,
            allowed_roles=RAID_WRITE_ROLES,
        )
        changes = item_in.model_dump(exclude_unset=True)
        self.validate_update_options(config=config, account_id=item.account_id, changes=changes)
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
        enriched_items: list[dict[str, object]] = []
        for item in items:
            item_data = {**item.__dict__}
            for option_field in option_fields:
                summary_field = option_field[:-3] if option_field.endswith("_id") else option_field
                item_data[summary_field] = self.option_summary(getattr(item, option_field), options)
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