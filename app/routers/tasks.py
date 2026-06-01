from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session
from typing import Literal

from app.core.database import get_db
from app.core.pagination import PaginatedResponse, PaginationParams, get_pagination_params
from app.models.user import User
from app.schemas.tasks import (
    TaskAssignmentCreate,
    TaskAssignmentRead,
    TaskBulkDeleteRequest,
    TaskBulkUpdateRequest,
    DueTasksRead,
    ProjectTaskGanttRead,
    TaskBoardPositionUpdate,
    TaskBoardRead,
    TaskCreate,
    TaskMoveRequest,
    TaskPredecessorCreate,
    TaskPredecessorRead,
    TaskRead,
    TaskReorderRequest,
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


@router.get("/projects/{project_id}/board", response_model=TaskBoardRead)
def get_project_board(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return TaskService(db).get_project_board(project_id=project_id, current_user=current_user)


@router.get("/projects/{project_id}/gantt", response_model=ProjectTaskGanttRead)
def get_project_gantt(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return TaskService(db).get_project_gantt(project_id=project_id, current_user=current_user)


@router.get("/sprints/{sprint_id}/board", response_model=TaskBoardRead)
def get_sprint_board(
    sprint_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return TaskService(db).get_sprint_board(sprint_id=sprint_id, current_user=current_user)


@router.get("/accounts/{account_id}/tasks/due", response_model=DueTasksRead)
def list_due_tasks(
    account_id: UUID,
    mode: Literal["OVERDUE", "UPCOMING", "BOTH"] = Query(default="BOTH"),
    days: int = Query(default=30, ge=0),
    project_id: UUID | None = None,
    program_id: UUID | None = None,
    assigned_to: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return TaskService(db).list_due_tasks(
        account_id=account_id,
        current_user=current_user,
        mode=mode,
        days=days,
        project_id=project_id,
        program_id=program_id,
        assigned_to=assigned_to,
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


@router.patch(
    "/projects/{project_id}/tasks/bulk",
    response_model=list[TaskRead],
    summary="Bulk update project tasks",
    description=(
        "Apply spreadsheet-style updates to multiple tasks in one transaction. "
        "If any update is invalid, all changes are rolled back."
    ),
)
def bulk_update_tasks(
    project_id: UUID,
    bulk_in: TaskBulkUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TaskRead]:
    return TaskService(db).bulk_update_tasks(project_id=project_id, bulk_in=bulk_in, current_user=current_user)


@router.delete(
    "/projects/{project_id}/tasks/bulk",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Bulk delete project tasks",
    description=(
        "Delete multiple tasks from a project in one transaction. "
        "If any task is invalid, no tasks are deleted."
    ),
)
def bulk_delete_tasks(
    project_id: UUID,
    bulk_in: TaskBulkDeleteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    TaskService(db).bulk_delete_tasks(project_id=project_id, bulk_in=bulk_in, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/projects/{project_id}/tasks/reorder",
    response_model=list[TaskRead],
    summary="Reorder project tasks",
    description="Atomically update parent_task_id and sort_order for tasks in a project while preventing cycles.",
)
def reorder_tasks(
    project_id: UUID,
    reorder_in: TaskReorderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TaskRead]:
    return TaskService(db).reorder_tasks(project_id=project_id, reorder_in=reorder_in, current_user=current_user)


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


@router.patch("/tasks/{task_id}/board-position", response_model=TaskRead)
def update_task_board_position(
    task_id: UUID,
    position_in: TaskBoardPositionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    return TaskService(db).update_board_position(task_id=task_id, position_in=position_in, current_user=current_user)


@router.post(
    "/tasks/{task_id}/move",
    response_model=TaskRead,
    summary="Move a task",
    description="Move one task to a new parent and sort order while preventing self-parenting and cycles.",
)
def move_task(
    task_id: UUID,
    move_in: TaskMoveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    return TaskService(db).move_task(task_id=task_id, move_in=move_in, current_user=current_user)


@router.post(
    "/tasks/{task_id}/indent",
    response_model=TaskRead,
    summary="Indent a task",
    description="Make the task a child of its previous sibling within the same parent.",
)
def indent_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    return TaskService(db).indent_task(task_id=task_id, current_user=current_user)


@router.post(
    "/tasks/{task_id}/outdent",
    response_model=TaskRead,
    summary="Outdent a task",
    description="Move a task one hierarchy level up and place it immediately after its previous parent.",
)
def outdent_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    return TaskService(db).outdent_task(task_id=task_id, current_user=current_user)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    TaskService(db).delete_task(task_id=task_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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