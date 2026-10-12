from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.notifications import (
    NotificationListResponse,
    NotificationPreferencesRead,
    NotificationPreferencesUpdate,
    NotificationRead,
)
from app.services.auth import get_current_user
from app.services.notification_preferences import NotificationPreferenceService
from app.services.notifications import NotificationService


router = APIRouter(prefix="/api/v1", tags=["notifications"])


@router.get("/notifications", response_model=NotificationListResponse)
def list_notifications(
    unread_only: bool = Query(default=False, alias="unreadOnly"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return NotificationService(db).list_notifications(
        current_user=current_user,
        unread_only=unread_only,
        page=page,
        page_size=page_size,
    )


@router.patch("/notifications/{notification_id}/read", response_model=NotificationRead)
def mark_notification_read(
    notification_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationRead:
    return NotificationService(db).mark_read(notification_id=notification_id, current_user=current_user)


@router.patch("/notifications/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, int]:
    return NotificationService(db).mark_all_read(current_user=current_user)

@router.get("/me/notification-preferences", response_model=NotificationPreferencesRead)
def read_notification_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return NotificationPreferenceService(db).read(current_user)


@router.patch("/me/notification-preferences", response_model=NotificationPreferencesRead)
def update_notification_preferences(
    preferences_in: NotificationPreferencesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    changes = {field: value for field, value in preferences_in.model_dump(exclude_unset=True).items() if value is not None}
    return NotificationPreferenceService(db).update(current_user, changes)
