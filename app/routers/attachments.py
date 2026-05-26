from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.attachments import (
    AttachmentPresignedDownloadRead,
    AttachmentPresignedUploadCreate,
    AttachmentPresignedUploadRead,
    AttachmentRead,
)
from app.services.attachments import AttachmentService
from app.services.auth import get_current_user


router = APIRouter(prefix="/api/v1", tags=["attachments"])


@router.post(
    "/entities/{entity_type}/{entity_id}/attachments/presigned-upload",
    response_model=AttachmentPresignedUploadRead,
    status_code=status.HTTP_201_CREATED,
)
def create_presigned_upload(
    entity_type: str,
    entity_id: UUID,
    attachment_in: AttachmentPresignedUploadCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(get_current_user),
) -> AttachmentPresignedUploadRead:
    return AttachmentService(db, settings).create_presigned_upload(
        entity_type=entity_type,
        entity_id=entity_id,
        attachment_in=attachment_in,
        current_user=current_user,
    )


@router.get("/entities/{entity_type}/{entity_id}/attachments", response_model=list[AttachmentRead])
def list_attachments(
    entity_type: str,
    entity_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(get_current_user),
) -> list[AttachmentRead]:
    return AttachmentService(db, settings).list_attachments(
        entity_type=entity_type,
        entity_id=entity_id,
        current_user=current_user,
    )


@router.get("/attachments/{attachment_id}/presigned-download", response_model=AttachmentPresignedDownloadRead)
def create_presigned_download(
    attachment_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(get_current_user),
) -> AttachmentPresignedDownloadRead:
    return AttachmentService(db, settings).create_presigned_download(
        attachment_id=attachment_id,
        current_user=current_user,
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    attachment_id: UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current_user: User = Depends(get_current_user),
) -> Response:
    AttachmentService(db, settings).delete_attachment(attachment_id=attachment_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)