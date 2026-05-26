import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.pagination import PaginationParams, paginated_response, validate_sort
from app.models.account_member import AccountMemberRole
from app.models.assumption import Assumption
from app.models.attachment import Attachment
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
from app.repositories.attachments import AttachmentRepository
from app.schemas.attachments import AttachmentPresignedUploadCreate
from app.services.activity import ActivityLogService


logger = logging.getLogger(__name__)

ATTACHMENT_WRITE_ROLES = {
    AccountMemberRole.OWNER.value,
    AccountMemberRole.ADMIN.value,
    AccountMemberRole.MANAGER.value,
    AccountMemberRole.MEMBER.value,
}

ATTACHMENT_ENTITY_MODELS: dict[str, type[Any]] = {
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


@dataclass(frozen=True)
class ResolvedAttachmentEntity:
    entity_type: str
    entity_id: UUID
    account_id: UUID


def get_s3_client(settings: Settings) -> Any:
    import boto3

    return boto3.client("s3", region_name=settings.aws_region)


class AttachmentService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.attachments = AttachmentRepository(db)

    def create_presigned_upload(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        attachment_in: AttachmentPresignedUploadCreate,
        current_user: User,
    ) -> dict[str, object]:
        target = self.resolve_attachment_entity(entity_type=entity_type, entity_id=entity_id)
        self.require_account_role(
            account_id=target.account_id,
            user_id=current_user.id,
            allowed_roles=ATTACHMENT_WRITE_ROLES,
        )
        self.require_s3_bucket()

        attachment_id = uuid4()
        safe_file_name = self.safe_file_name(attachment_in.file_name)
        s3_key = f"accounts/{target.account_id}/{target.entity_type}/{target.entity_id}/{attachment_id}/{safe_file_name}"
        attachment = self.attachments.create(
            id=attachment_id,
            account_id=target.account_id,
            entity_type=target.entity_type,
            entity_id=target.entity_id,
            file_name=attachment_in.file_name,
            s3_key=s3_key,
            content_type=attachment_in.content_type,
            size_bytes=attachment_in.size_bytes,
            uploaded_by=current_user.id,
        )

        headers = {"Content-Type": attachment.content_type} if attachment.content_type else {}
        params: dict[str, object] = {"Bucket": self.settings.s3_bucket_name, "Key": attachment.s3_key}
        if attachment.content_type:
            params["ContentType"] = attachment.content_type
        upload_url = get_s3_client(self.settings).generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=self.settings.attachment_upload_expires_seconds,
            HttpMethod="PUT",
        )
        ActivityLogService(self.db).record(
            account_id=attachment.account_id,
            entity_type=attachment.entity_type,
            entity_id=attachment.entity_id,
            action="ATTACHMENT_ADDED",
            new_values={
                "attachment_id": attachment.id,
                "file_name": attachment.file_name,
                "s3_key": attachment.s3_key,
            },
            created_by=current_user.id,
        )
        self.db.commit()
        self.db.refresh(attachment)
        return {
            "attachment_id": attachment.id,
            "s3_key": attachment.s3_key,
            "upload_url": upload_url,
            "method": "PUT",
            "headers": headers,
        }

    def list_attachments(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        current_user: User,
        sort: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> list[Attachment] | dict[str, object]:
        target = self.resolve_attachment_entity(entity_type=entity_type, entity_id=entity_id)
        self.require_account_member(account_id=target.account_id, user_id=current_user.id)
        sort_value = validate_sort(sort, allowed_fields={"created_at"}, default="-created_at")
        if pagination and pagination.paginated:
            attachments, total = self.attachments.list_for_entity_paginated(
                entity_type=target.entity_type,
                entity_id=target.entity_id,
                sort=sort_value,
                pagination=pagination,
            )
            return paginated_response(items=attachments, total=total, pagination=pagination)
        return self.attachments.list_for_entity(
            entity_type=target.entity_type,
            entity_id=target.entity_id,
            sort=sort_value,
        )

    def create_presigned_download(self, *, attachment_id: UUID, current_user: User) -> dict[str, str]:
        attachment = self.get_attachment_or_404(attachment_id)
        self.require_account_member(account_id=attachment.account_id, user_id=current_user.id)
        self.require_s3_bucket()
        download_url = get_s3_client(self.settings).generate_presigned_url(
            "get_object",
            Params={"Bucket": self.settings.s3_bucket_name, "Key": attachment.s3_key},
            ExpiresIn=self.settings.attachment_download_expires_seconds,
        )
        return {"download_url": download_url}

    def delete_attachment(self, *, attachment_id: UUID, current_user: User) -> None:
        attachment = self.get_attachment_or_404(attachment_id)
        self.require_account_role(
            account_id=attachment.account_id,
            user_id=current_user.id,
            allowed_roles=ATTACHMENT_WRITE_ROLES,
        )
        self.delete_s3_object_if_configured(attachment)
        ActivityLogService(self.db).record(
            account_id=attachment.account_id,
            entity_type=attachment.entity_type,
            entity_id=attachment.entity_id,
            action="ATTACHMENT_REMOVED",
            old_values={
                "attachment_id": attachment.id,
                "file_name": attachment.file_name,
                "s3_key": attachment.s3_key,
            },
            created_by=current_user.id,
        )
        self.attachments.delete(attachment)
        self.db.commit()

    def resolve_attachment_entity(self, *, entity_type: str, entity_id: UUID) -> ResolvedAttachmentEntity:
        normalized_entity_type = entity_type.strip().upper()
        model_cls = ATTACHMENT_ENTITY_MODELS.get(normalized_entity_type)
        if model_cls is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported attachment entity type.",
            )
        target = self.db.get(model_cls, entity_id)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment target not found.")
        return ResolvedAttachmentEntity(
            entity_type=normalized_entity_type,
            entity_id=entity_id,
            account_id=target.account_id,
        )

    def get_attachment_or_404(self, attachment_id: UUID) -> Attachment:
        attachment = self.attachments.get(attachment_id)
        if attachment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found.")
        return attachment

    def require_s3_bucket(self) -> None:
        if not self.settings.s3_bucket_name:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="S3 attachment bucket is not configured.",
            )

    def delete_s3_object_if_configured(self, attachment: Attachment) -> None:
        if not self.settings.s3_bucket_name:
            return
        try:
            get_s3_client(self.settings).delete_object(
                Bucket=self.settings.s3_bucket_name,
                Key=attachment.s3_key,
            )
        except Exception:
            logger.warning("Failed to delete S3 object for attachment %s", attachment.id, exc_info=True)

    def safe_file_name(self, file_name: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name.strip()).strip("._-")
        if not sanitized:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="file_name cannot be empty.")
        return sanitized[:255]

    def require_account_member(self, *, account_id: UUID, user_id: UUID) -> str:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")
        return membership.role

    def require_account_role(self, *, account_id: UUID, user_id: UUID, allowed_roles: set[str]) -> str:
        membership_role = self.require_account_member(account_id=account_id, user_id=user_id)
        if membership_role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")
        return membership_role