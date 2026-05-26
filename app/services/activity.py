from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.account_member import AccountMemberRole
from app.models.activity_log import ActivityLog
from app.models.assumption import Assumption
from app.models.comment import Comment
from app.models.decision import Decision
from app.models.decision_option import DecisionOption
from app.models.issue import Issue
from app.models.portfolio import Portfolio
from app.models.program import Program
from app.models.project import Project
from app.models.risk import Risk
from app.models.task import Task
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.activity import ActivityLogRepository


ACTIVITY_ENTITY_MODELS: dict[str, type[Any]] = {
    "ACCOUNT": Account,
    "PORTFOLIO": Portfolio,
    "PROGRAM": Program,
    "PROJECT": Project,
    "TASK": Task,
    "RISK": Risk,
    "ISSUE": Issue,
    "ASSUMPTION": Assumption,
    "DECISION": Decision,
    "DECISION_OPTION": DecisionOption,
    "COMMENT": Comment,
}

ACTIVITY_ACTIONS = {
    "CREATED",
    "UPDATED",
    "DELETED",
    "COMMENTED",
    "STATUS_CHANGED",
    "ASSIGNED",
    "OPTION_ADDED",
    "OPTION_UPDATED",
    "OPTION_REMOVED",
    "ATTACHMENT_ADDED",
    "ATTACHMENT_REMOVED",
}


@dataclass(frozen=True)
class ResolvedActivityEntity:
    entity_type: str
    entity_id: UUID
    account_id: UUID


class ActivityLogService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.activity = ActivityLogRepository(db)

    def record(
        self,
        *,
        account_id: UUID,
        entity_type: str,
        entity_id: UUID,
        action: str,
        old_values: dict[str, object] | None = None,
        new_values: dict[str, object] | None = None,
        created_by: UUID | None = None,
    ) -> ActivityLog:
        normalized_entity_type = entity_type.strip().upper()
        normalized_action = action.strip().upper()
        if normalized_entity_type not in ACTIVITY_ENTITY_MODELS:
            raise ValueError(f"Unsupported activity entity type: {entity_type}")
        if normalized_action not in ACTIVITY_ACTIONS:
            raise ValueError(f"Unsupported activity action: {action}")
        return self.activity.create(
            account_id=account_id,
            entity_type=normalized_entity_type,
            entity_id=entity_id,
            action=normalized_action,
            old_values=self.json_safe(old_values),
            new_values=self.json_safe(new_values),
            created_by=created_by,
        )

    def list_entity_activity(self, *, entity_type: str, entity_id: UUID, current_user: User) -> list[ActivityLog]:
        target = self.resolve_activity_entity(entity_type=entity_type, entity_id=entity_id)
        self.require_account_member(account_id=target.account_id, user_id=current_user.id)
        return self.activity.list_for_entity(entity_type=target.entity_type, entity_id=target.entity_id)

    def list_project_activity(self, *, project_id: UUID, current_user: User) -> list[ActivityLog]:
        project = self.db.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
        self.require_account_member(account_id=project.account_id, user_id=current_user.id)
        return self.activity.list_for_project(project.id)

    def resolve_activity_entity(self, *, entity_type: str, entity_id: UUID) -> ResolvedActivityEntity:
        normalized_entity_type = entity_type.strip().upper()
        model_cls = ACTIVITY_ENTITY_MODELS.get(normalized_entity_type)
        if model_cls is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported activity entity type.",
            )
        target = self.db.get(model_cls, entity_id)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity target not found.")
        account_id = target.id if normalized_entity_type == "ACCOUNT" else target.account_id
        return ResolvedActivityEntity(
            entity_type=normalized_entity_type,
            entity_id=entity_id,
            account_id=account_id,
        )

    def require_account_member(self, *, account_id: UUID, user_id: UUID) -> None:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")

    def json_safe(self, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, dict):
            return {str(key): self.json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self.json_safe(item) for item in value]
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        return value