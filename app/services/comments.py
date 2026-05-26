from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.account_member import AccountMemberRole
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
from app.repositories.comments import CommentRepository
from app.schemas.comments import CommentCreate, CommentUpdate


COMMENT_WRITE_ROLES = {
    AccountMemberRole.OWNER.value,
    AccountMemberRole.ADMIN.value,
    AccountMemberRole.MANAGER.value,
    AccountMemberRole.MEMBER.value,
}

COMMENT_ADMIN_ROLES = {AccountMemberRole.OWNER.value, AccountMemberRole.ADMIN.value}

COMMENT_ENTITY_MODELS: dict[str, type[Any]] = {
    "PORTFOLIO": Portfolio,
    "PROGRAM": Program,
    "PROJECT": Project,
    "TASK": Task,
    "RISK": Risk,
    "ISSUE": Issue,
    "ASSUMPTION": Assumption,
    "DECISION": Decision,
    "DECISION_OPTION": DecisionOption,
}


@dataclass(frozen=True)
class ResolvedCommentEntity:
    entity_type: str
    entity_id: UUID
    account_id: UUID


class CommentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.comments = CommentRepository(db)

    def list_comments(self, *, entity_type: str, entity_id: UUID, current_user: User) -> list[Comment]:
        target = self.resolve_comment_entity(entity_type=entity_type, entity_id=entity_id)
        self.require_account_member(account_id=target.account_id, user_id=current_user.id)
        return self.comments.list_for_entity(entity_type=target.entity_type, entity_id=target.entity_id)

    def create_comment(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        comment_in: CommentCreate,
        current_user: User,
    ) -> Comment:
        target = self.resolve_comment_entity(entity_type=entity_type, entity_id=entity_id)
        self.require_account_role(
            account_id=target.account_id,
            user_id=current_user.id,
            allowed_roles=COMMENT_WRITE_ROLES,
        )
        comment = self.comments.create(
            account_id=target.account_id,
            entity_type=target.entity_type,
            entity_id=target.entity_id,
            body=comment_in.body,
            created_by=current_user.id,
        )
        self.db.commit()
        self.db.refresh(comment)
        return comment

    def update_comment(self, *, comment_id: UUID, comment_in: CommentUpdate, current_user: User) -> Comment:
        comment = self.get_comment_or_404(comment_id)
        membership_role = self.require_account_role(
            account_id=comment.account_id,
            user_id=current_user.id,
            allowed_roles=COMMENT_WRITE_ROLES,
        )
        self.require_comment_owner_or_admin(
            comment=comment,
            current_user=current_user,
            membership_role=membership_role,
        )
        comment = self.comments.update(comment, body=comment_in.body)
        self.db.commit()
        self.db.refresh(comment)
        return comment

    def delete_comment(self, *, comment_id: UUID, current_user: User) -> None:
        comment = self.get_comment_or_404(comment_id)
        membership_role = self.require_account_role(
            account_id=comment.account_id,
            user_id=current_user.id,
            allowed_roles=COMMENT_WRITE_ROLES,
        )
        self.require_comment_owner_or_admin(
            comment=comment,
            current_user=current_user,
            membership_role=membership_role,
        )
        self.comments.delete(comment)
        self.db.commit()

    def resolve_comment_entity(self, *, entity_type: str, entity_id: UUID) -> ResolvedCommentEntity:
        normalized_entity_type = entity_type.strip().upper()
        model_cls = COMMENT_ENTITY_MODELS.get(normalized_entity_type)
        if model_cls is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported comment entity type.",
            )

        target = self.db.get(model_cls, entity_id)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment target not found.")
        return ResolvedCommentEntity(
            entity_type=normalized_entity_type,
            entity_id=entity_id,
            account_id=target.account_id,
        )

    def get_comment_or_404(self, comment_id: UUID) -> Comment:
        comment = self.comments.get(comment_id)
        if comment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found.")
        return comment

    def require_comment_owner_or_admin(
        self,
        *,
        comment: Comment,
        current_user: User,
        membership_role: str,
    ) -> None:
        if membership_role in COMMENT_ADMIN_ROLES:
            return
        if comment.created_by == current_user.id:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify another user's comment.",
        )

    def require_account_member(self, *, account_id: UUID, user_id: UUID) -> str:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")
        return membership.role

    def require_account_role(
        self,
        *,
        account_id: UUID,
        user_id: UUID,
        allowed_roles: set[str],
    ) -> str:
        membership_role = self.require_account_member(account_id=account_id, user_id=user_id)
        if membership_role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")
        return membership_role