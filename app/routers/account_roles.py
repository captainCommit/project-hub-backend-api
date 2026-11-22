from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.account_roles import AccountRoleCreate, AccountRoleRead, AccountRoleUpdate
from app.services.account_roles import AccountRoleService
from app.services.auth import get_current_user


router = APIRouter(prefix="/api/v1", tags=["roles"])


@router.get("/accounts/{account_id}/roles", response_model=list[AccountRoleRead], summary="The account's roles and their rights")
def list_roles(
    account_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[AccountRoleRead]:
    return AccountRoleService(db).list_roles(account_id=account_id, current_user=current_user)


@router.post(
    "/accounts/{account_id}/roles",
    response_model=AccountRoleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a custom role (owners)",
)
def create_role(
    account_id: UUID,
    role_in: AccountRoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountRoleRead:
    return AccountRoleService(db).create_role(account_id=account_id, role_in=role_in, current_user=current_user)


@router.patch("/account-roles/{role_id}", response_model=AccountRoleRead, summary="Change a role's rights or scope (owners)")
def update_role(
    role_id: UUID,
    role_in: AccountRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccountRoleRead:
    return AccountRoleService(db).update_role(role_id=role_id, role_in=role_in, current_user=current_user)


@router.post("/account-roles/{role_id}/reset", response_model=AccountRoleRead, summary="Put a built-in role back to its default (owners)")
def reset_role(
    role_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> AccountRoleRead:
    return AccountRoleService(db).reset_role(role_id=role_id, current_user=current_user)


@router.delete("/account-roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an unused custom role (owners)")
def delete_role(role_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> Response:
    AccountRoleService(db).delete_role(role_id=role_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
