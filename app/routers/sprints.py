from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.sprints import SprintCreate, SprintRead, SprintUpdate
from app.services.auth import get_current_user
from app.services.sprints import SprintService


router = APIRouter(prefix="/api/v1", tags=["sprints"])


@router.get("/projects/{project_id}/sprints", response_model=list[SprintRead])
def list_sprints(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    return SprintService(db).list_sprints(project_id=project_id, current_user=current_user)


@router.post("/projects/{project_id}/sprints", response_model=SprintRead, status_code=status.HTTP_201_CREATED)
def create_sprint(
    project_id: UUID,
    sprint_in: SprintCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return SprintService(db).create_sprint(project_id=project_id, sprint_in=sprint_in, current_user=current_user)


@router.get("/sprints/{sprint_id}", response_model=SprintRead)
def get_sprint(
    sprint_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return SprintService(db).get_sprint(sprint_id=sprint_id, current_user=current_user)


@router.patch("/sprints/{sprint_id}", response_model=SprintRead)
def update_sprint(
    sprint_id: UUID,
    sprint_in: SprintUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return SprintService(db).update_sprint(sprint_id=sprint_id, sprint_in=sprint_in, current_user=current_user)


@router.delete("/sprints/{sprint_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def delete_sprint(sprint_id: UUID) -> dict[str, str]:
    return {"detail": "Sprint deletion is not implemented in Phase 10D."}