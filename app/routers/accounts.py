from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.account import AccountCreate, AccountRead, AccountUpdate
from app.services.accounts import AccountService
from app.services.auth import get_current_user


router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
def create_account(
    account_in: AccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountRead:
    return AccountService(db).create_account(account_in=account_in, current_user=current_user)


@router.get("", response_model=list[AccountRead])
def list_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AccountRead]:
    return AccountService(db).list_accounts_for_user(current_user=current_user)


@router.get("/{account_id}", response_model=AccountRead)
def get_account(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountRead:
    return AccountService(db).get_account_for_user(
        account_id=account_id,
        current_user=current_user,
    )


@router.patch("/{account_id}", response_model=AccountRead)
def update_account(
    account_id: UUID,
    account_in: AccountUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountRead:
    return AccountService(db).update_account(
        account_id=account_id,
        account_in=account_in,
        current_user=current_user,
    )


@router.delete("/{account_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def delete_account(account_id: UUID) -> dict[str, str]:
    return {"detail": "Account deletion is not implemented in Phase 1A."}