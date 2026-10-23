from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.dashboard import AccountDashboardRead
from app.schemas.home import HomeNotificationsDismiss, HomeNotificationsDismissResult, HomeNotificationsReadResult, HomeRead
from app.services.account_dashboard import AccountDashboardService
from app.services.auth import get_current_user
from app.services.home import HomeService


router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get("/accounts/{account_id}/dashboard", response_model=AccountDashboardRead)
def get_account_dashboard(
    account_id: UUID,
    portfolio_id: UUID | None = None,
    program_id: UUID | None = None,
    project_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return AccountDashboardService(db).get_account_dashboard(
        account_id=account_id,
        current_user=current_user,
        portfolio_id=portfolio_id,
        program_id=program_id,
        project_id=project_id,
    )


@router.get("/accounts/{account_id}/me/home", response_model=HomeRead, summary="Your home page: dated work, your projects and recent activity")
def get_my_home(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return HomeService(db).get_home(account_id=account_id, current_user=current_user)


@router.post(
    "/accounts/{account_id}/me/home/notifications/read",
    response_model=HomeNotificationsReadResult,
    summary="Mark everything in your Home page bell as read",
)
def mark_home_notifications_read(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return HomeService(db).mark_notifications_read(account_id=account_id, current_user=current_user)


@router.post(
    "/accounts/{account_id}/me/home/notifications/dismiss",
    response_model=HomeNotificationsDismissResult,
    summary="Remove items from your Home page bell",
)
def dismiss_home_notifications(
    account_id: UUID,
    body: HomeNotificationsDismiss,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return HomeService(db).dismiss_notifications(account_id=account_id, current_user=current_user, item_ids=body.ids)
