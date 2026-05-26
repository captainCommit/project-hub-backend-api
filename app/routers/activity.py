from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.pagination import PaginatedResponse, PaginationParams, get_pagination_params
from app.models.user import User
from app.schemas.activity import ActivityLogRead
from app.services.activity import ActivityLogService
from app.services.auth import get_current_user


router = APIRouter(prefix="/api/v1", tags=["activity"])


@router.get("/entities/{entity_type}/{entity_id}/activity", response_model=list[ActivityLogRead] | PaginatedResponse[ActivityLogRead])
def list_entity_activity(
    entity_type: str,
    entity_id: UUID,
    sort: str | None = Query(default=None),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ActivityLogRead] | dict[str, object]:
    return ActivityLogService(db).list_entity_activity(
        entity_type=entity_type,
        entity_id=entity_id,
        current_user=current_user,
        sort=sort,
        pagination=pagination,
    )


@router.get("/projects/{project_id}/activity", response_model=list[ActivityLogRead] | PaginatedResponse[ActivityLogRead])
def list_project_activity(
    project_id: UUID,
    sort: str | None = Query(default=None),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ActivityLogRead] | dict[str, object]:
    return ActivityLogService(db).list_project_activity(
        project_id=project_id,
        current_user=current_user,
        sort=sort,
        pagination=pagination,
    )