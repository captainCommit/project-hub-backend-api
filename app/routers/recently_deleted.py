from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.recently_deleted import DeletedItemRead, RestoredItemRead
from app.services.auth import get_current_user
from app.services.recently_deleted import RecentlyDeletedService


router = APIRouter(prefix="/api/v1", tags=["recently deleted"])


@router.get(
    "/accounts/{account_id}/recently-deleted",
    response_model=list[DeletedItemRead],
    summary="Deleted portfolios, programs and projects that can still be restored (owners see all; others their own)",
)
def list_recently_deleted(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DeletedItemRead]:
    return RecentlyDeletedService(db).list(account_id=account_id, current_user=current_user)


@router.post(
    "/recently-deleted/{record_id}/restore",
    response_model=RestoredItemRead,
    summary="Put a deleted portfolio, program or project back with everything that was under it",
)
def restore_deleted_item(
    record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RestoredItemRead:
    return RecentlyDeletedService(db).restore(record_id=record_id, current_user=current_user)


@router.delete(
    "/recently-deleted/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete an item now instead of after 30 days",
)
def delete_deleted_item(
    record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    RecentlyDeletedService(db).delete_permanently(record_id=record_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
