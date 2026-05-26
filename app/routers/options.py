from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.options import (
    OptionSetCreate,
    OptionSetRead,
    OptionSetUpdate,
    OptionSetWithValuesRead,
    OptionValueCreate,
    OptionValueRead,
    OptionValueUpdate,
)
from app.services.auth import get_current_user
from app.services.options import OptionService


router = APIRouter(prefix="/api/v1", tags=["options"])


@router.get("/accounts/{account_id}/option-sets", response_model=list[OptionSetRead])
def list_option_sets(
    account_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[OptionSetRead]:
    return OptionService(db).list_option_sets(account_id=account_id, current_user=current_user)


@router.post(
    "/accounts/{account_id}/option-sets",
    response_model=OptionSetRead,
    status_code=status.HTTP_201_CREATED,
)
def create_option_set(
    account_id: UUID,
    option_set_in: OptionSetCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OptionSetRead:
    return OptionService(db).create_option_set(
        account_id=account_id,
        option_set_in=option_set_in,
        current_user=current_user,
    )


@router.patch("/option-sets/{option_set_id}", response_model=OptionSetRead)
def update_option_set(
    option_set_id: UUID,
    option_set_in: OptionSetUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OptionSetRead:
    return OptionService(db).update_option_set(
        option_set_id=option_set_id,
        option_set_in=option_set_in,
        current_user=current_user,
    )


@router.delete("/option-sets/{option_set_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def delete_option_set(option_set_id: UUID) -> dict[str, str]:
    return {"detail": "Option set deletion is not implemented in Phase 2."}


@router.get("/accounts/{account_id}/options", response_model=list[OptionSetWithValuesRead])
def list_options(
    account_id: UUID,
    entity_type: str | None = Query(default=None, alias="entityType"),
    name: str | None = None,
    include_inactive: bool = Query(default=False, alias="includeInactive"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[OptionSetWithValuesRead]:
    return OptionService(db).list_options(
        account_id=account_id,
        current_user=current_user,
        entity_type=entity_type,
        name=name,
        include_inactive=include_inactive,
    )


@router.get("/option-sets/{option_set_id}/values", response_model=list[OptionValueRead])
def list_option_values(
    option_set_id: UUID,
    include_inactive: bool = Query(default=False, alias="includeInactive"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[OptionValueRead]:
    return OptionService(db).list_option_values(
        option_set_id=option_set_id,
        current_user=current_user,
        include_inactive=include_inactive,
    )


@router.post(
    "/option-sets/{option_set_id}/values",
    response_model=OptionValueRead,
    status_code=status.HTTP_201_CREATED,
)
def create_option_value(
    option_set_id: UUID,
    option_value_in: OptionValueCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OptionValueRead:
    return OptionService(db).create_option_value(
        option_set_id=option_set_id,
        option_value_in=option_value_in,
        current_user=current_user,
    )


@router.patch("/option-values/{option_value_id}", response_model=OptionValueRead)
def update_option_value(
    option_value_id: UUID,
    option_value_in: OptionValueUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OptionValueRead:
    return OptionService(db).update_option_value(
        option_value_id=option_value_id,
        option_value_in=option_value_in,
        current_user=current_user,
    )


@router.delete("/option-values/{option_value_id}", response_model=OptionValueRead)
def deactivate_option_value(
    option_value_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OptionValueRead:
    return OptionService(db).deactivate_option_value(
        option_value_id=option_value_id,
        current_user=current_user,
    )