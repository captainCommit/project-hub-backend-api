from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.access import AccessGrantCreate, AccessGrantRead, AccessGrantUpdate, MyAccessRead
from app.services.access_grants import AccessGrantService
from app.services.auth import get_current_user


router = APIRouter(prefix="/api/v1", tags=["access"])


@router.get(
    "/accounts/{account_id}/access-grants",
    response_model=list[AccessGrantRead],
    summary="Who has access to a portfolio, program or project (or, for owners, the whole account)",
)
def list_access_grants(
    account_id: UUID,
    portfolio_id: UUID | None = Query(default=None),
    program_id: UUID | None = Query(default=None),
    project_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AccessGrantRead]:
    return AccessGrantService(db).list_grants(
        account_id=account_id,
        current_user=current_user,
        portfolio_id=portfolio_id,
        program_id=program_id,
        project_id=project_id,
    )


@router.post(
    "/accounts/{account_id}/access-grants",
    response_model=AccessGrantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Give someone a role on a portfolio, program or project (replaces their role there)",
)
def create_access_grant(
    account_id: UUID,
    grant_in: AccessGrantCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccessGrantRead:
    return AccessGrantService(db).create_grant(account_id=account_id, grant_in=grant_in, current_user=current_user)


@router.patch("/access-grants/{grant_id}", response_model=AccessGrantRead)
def update_access_grant(
    grant_id: UUID,
    grant_in: AccessGrantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AccessGrantRead:
    return AccessGrantService(db).update_grant(grant_id=grant_id, grant_in=grant_in, current_user=current_user)


@router.delete("/access-grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_access_grant(
    grant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    AccessGrantService(db).delete_grant(grant_id=grant_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/accounts/{account_id}/my-access",
    response_model=MyAccessRead,
    summary="The current user's permissions on everything they can see in the account",
)
def get_my_access(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MyAccessRead:
    return AccessGrantService(db).my_access(account_id=account_id, current_user=current_user)
