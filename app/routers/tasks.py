from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.pagination import PaginatedResponse, PaginationParams, get_pagination_params
from app.models.user import User
from app.schemas.tasks import (
    TaskAssignmentCreate,
    TaskAssignmentRead,
    TaskCreate,
    TaskPredecessorCreate,
    TaskPredecessorRead,
    TaskRead,
    TaskTreeRead,
    TaskUpdate,
)
from app.services.auth import get_current_user
from app.services.tasks import TaskService


router = APIRouter(prefix="/api/v1", tags=["tasks"])


@router.get("/projects/{project_id}/tasks", response_model=list[TaskRead] | PaginatedResponse[TaskRead])
def list_tasks(
    project_id: UUID,
    status_id: UUID | None = None,
    task_type_id: UUID | None = None,
    sort: str | None = Query(default=None),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TaskRead] | dict[str, object]:
    return TaskService(db).list_tasks(
        project_id=project_id,
        current_user=current_user,
        status_id=status_id,
        task_type_id=task_type_id,
        sort=sort,
        pagination=pagination,
    )


@router.get("/projects/{project_id}/tasks/tree", response_model=list[TaskTreeRead])
def get_task_tree(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TaskTreeRead]:
    return TaskService(db).build_task_tree(project_id=project_id, current_user=current_user)


@router.post("/projects/{project_id}/tasks", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(
    project_id: UUID,
    task_in: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    return TaskService(db).create_task(project_id=project_id, task_in=task_in, current_user=current_user)


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    return TaskService(db).get_task(task_id=task_id, current_user=current_user)


@router.patch("/tasks/{task_id}", response_model=TaskRead)
def update_task(
    task_id: UUID,
    task_in: TaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    return TaskService(db).update_task(task_id=task_id, task_in=task_in, current_user=current_user)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def delete_task(task_id: UUID) -> dict[str, str]:
    return {"detail": "Task deletion is not implemented in Phase 3B."}


@router.post(
    "/tasks/{task_id}/assignments",
    response_model=TaskAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task_assignment(
    task_id: UUID,
    assignment_in: TaskAssignmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskAssignmentRead:
    return TaskService(db).create_assignment(
        task_id=task_id,
        assignment_in=assignment_in,
        current_user=current_user,
    )


@router.delete("/tasks/{task_id}/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task_assignment(
    task_id: UUID,
    assignment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    TaskService(db).delete_assignment(
        task_id=task_id,
        assignment_id=assignment_id,
        current_user=current_user,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/tasks/{task_id}/predecessors",
    response_model=TaskPredecessorRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task_predecessor(
    task_id: UUID,
    predecessor_in: TaskPredecessorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskPredecessorRead:
    return TaskService(db).create_predecessor(
        task_id=task_id,
        predecessor_in=predecessor_in,
        current_user=current_user,
    )


@router.delete("/tasks/{task_id}/predecessors/{predecessor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task_predecessor(
    task_id: UUID,
    predecessor_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    TaskService(db).delete_predecessor(
        task_id=task_id,
        predecessor_id=predecessor_id,
        current_user=current_user,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)