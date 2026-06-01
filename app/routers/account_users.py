from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import Settings, get_settings
from app.core.pagination import PaginatedResponse, PaginationParams, get_pagination_params
from app.models.user import User
from app.schemas.account_user import (
    AccountUserBulkInviteCreate,
    AccountUserBulkInviteRead,
    AccountUserInviteCreate,
    AccountUserInviteRead,
    AccountUserRead,
)
from app.services.account_users import AccountUserService
from app.services.auth import get_current_user


router = APIRouter(prefix="/api/v1/accounts", tags=["account-users"])


@router.get("/{account_id}/users", response_model=list[AccountUserRead] | PaginatedResponse[AccountUserRead])
def list_account_users(
    account_id: UUID,
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AccountUserRead] | dict[str, object]:
    return AccountUserService(db).list_account_users(
        account_id=account_id,
        current_user=current_user,
        pagination=pagination,
    )


@router.post("/{account_id}/users/invite", response_model=AccountUserInviteRead)
def invite_account_user(
    account_id: UUID,
    invite_in: AccountUserInviteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> AccountUserInviteRead:
    return AccountUserService(db, settings=settings).invite_account_user(
        account_id=account_id,
        invite_in=invite_in,
        current_user=current_user,
    )


@router.post("/{account_id}/users/bulk-invite", response_model=AccountUserBulkInviteRead)
def bulk_invite_account_users(
    account_id: UUID,
    bulk_in: AccountUserBulkInviteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> AccountUserBulkInviteRead:
    return AccountUserService(db, settings=settings).bulk_invite_account_users(
        account_id=account_id,
        bulk_in=bulk_in,
        current_user=current_user,
    )


@router.get("/{account_id}/users/search", response_model=list[AccountUserRead])
def search_account_users(
    account_id: UUID,
    q: str = Query(min_length=2),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AccountUserRead]:
    return AccountUserService(db).search_account_users(account_id=account_id, q=q, current_user=current_user)