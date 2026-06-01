from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.delivery import DeliveryOverviewRead
from app.services.auth import get_current_user
from app.services.delivery import DeliveryService


router = APIRouter(prefix="/api/v1", tags=["delivery"])


@router.get("/accounts/{account_id}/delivery/overview", response_model=DeliveryOverviewRead)
def get_delivery_overview(
    account_id: UUID,
    program_id: UUID | None = None,
    project_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return DeliveryService(db).get_delivery_overview(
        account_id=account_id,
        current_user=current_user,
        program_id=program_id,
        project_id=project_id,
    )