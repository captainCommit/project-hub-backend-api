from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.skills import (
    ResourceSkillCreate,
    ResourceSkillRead,
    ResourceSkillUpdate,
    SkillCreate,
    SkillRead,
    SkillUpdate,
    TaskRequiredSkillCreate,
    TaskRequiredSkillRead,
    TaskRequiredSkillUpdate,
)
from app.services.auth import get_current_user
from app.services.skills import SkillService


router = APIRouter(prefix="/api/v1", tags=["skills"])


@router.get("/accounts/{account_id}/skills", response_model=list[SkillRead])
def list_skills(
    account_id: UUID,
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SkillRead]:
    return SkillService(db).list_skills(
        account_id=account_id,
        current_user=current_user,
        include_inactive=include_inactive,
    )


@router.post("/accounts/{account_id}/skills", response_model=SkillRead, status_code=status.HTTP_201_CREATED)
def create_skill(
    account_id: UUID,
    skill_in: SkillCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SkillRead:
    return SkillService(db).create_skill(account_id=account_id, skill_in=skill_in, current_user=current_user)


@router.get("/skills/{skill_id}", response_model=SkillRead)
def get_skill(
    skill_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SkillRead:
    return SkillService(db).get_skill(skill_id=skill_id, current_user=current_user)


@router.patch("/skills/{skill_id}", response_model=SkillRead)
def update_skill(
    skill_id: UUID,
    skill_in: SkillUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SkillRead:
    return SkillService(db).update_skill(skill_id=skill_id, skill_in=skill_in, current_user=current_user)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(
    skill_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    SkillService(db).delete_skill(skill_id=skill_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/resources/{resource_id}/skills", response_model=list[ResourceSkillRead])
def list_resource_skills(
    resource_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    return SkillService(db).list_resource_skills(resource_id=resource_id, current_user=current_user)


@router.post("/resources/{resource_id}/skills", response_model=ResourceSkillRead, status_code=status.HTTP_201_CREATED)
def create_resource_skill(
    resource_id: UUID,
    resource_skill_in: ResourceSkillCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return SkillService(db).create_resource_skill(
        resource_id=resource_id,
        resource_skill_in=resource_skill_in,
        current_user=current_user,
    )


@router.patch("/resource-skills/{resource_skill_id}", response_model=ResourceSkillRead)
def update_resource_skill(
    resource_skill_id: UUID,
    resource_skill_in: ResourceSkillUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return SkillService(db).update_resource_skill(
        resource_skill_id=resource_skill_id,
        resource_skill_in=resource_skill_in,
        current_user=current_user,
    )


@router.delete("/resource-skills/{resource_skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resource_skill(
    resource_skill_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    SkillService(db).delete_resource_skill(resource_skill_id=resource_skill_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tasks/{task_id}/required-skills", response_model=list[TaskRequiredSkillRead])
def list_task_required_skills(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    return SkillService(db).list_task_required_skills(task_id=task_id, current_user=current_user)


@router.post(
    "/tasks/{task_id}/required-skills",
    response_model=TaskRequiredSkillRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task_required_skill(
    task_id: UUID,
    required_skill_in: TaskRequiredSkillCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return SkillService(db).create_task_required_skill(
        task_id=task_id,
        required_skill_in=required_skill_in,
        current_user=current_user,
    )


@router.patch("/task-required-skills/{required_skill_id}", response_model=TaskRequiredSkillRead)
def update_task_required_skill(
    required_skill_id: UUID,
    required_skill_in: TaskRequiredSkillUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return SkillService(db).update_task_required_skill(
        required_skill_id=required_skill_id,
        required_skill_in=required_skill_in,
        current_user=current_user,
    )


@router.delete("/task-required-skills/{required_skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task_required_skill(
    required_skill_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    SkillService(db).delete_task_required_skill(required_skill_id=required_skill_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)