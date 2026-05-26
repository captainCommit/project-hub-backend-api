from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.pagination import PaginatedResponse, PaginationParams, get_pagination_params
from app.models.user import User
from app.schemas.comments import CommentCreate, CommentMentionRead, CommentRead, CommentUpdate
from app.services.auth import get_current_user
from app.services.comments import CommentService
from app.services.mentions import MentionService


router = APIRouter(prefix="/api/v1", tags=["comments"])


@router.get("/entities/{entity_type}/{entity_id}/comments", response_model=list[CommentRead] | PaginatedResponse[CommentRead])
def list_comments(
    entity_type: str,
    entity_id: UUID,
    sort: str | None = Query(default=None),
    pagination: PaginationParams = Depends(get_pagination_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CommentRead] | dict[str, object]:
    return CommentService(db).list_comments(
        entity_type=entity_type,
        entity_id=entity_id,
        current_user=current_user,
        sort=sort,
        pagination=pagination,
    )


@router.post(
    "/entities/{entity_type}/{entity_id}/comments",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    entity_type: str,
    entity_id: UUID,
    comment_in: CommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentRead:
    return CommentService(db).create_comment(
        entity_type=entity_type,
        entity_id=entity_id,
        comment_in=comment_in,
        current_user=current_user,
    )


@router.patch("/comments/{comment_id}", response_model=CommentRead)
def update_comment(
    comment_id: UUID,
    comment_in: CommentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentRead:
    return CommentService(db).update_comment(
        comment_id=comment_id,
        comment_in=comment_in,
        current_user=current_user,
    )


@router.get("/comments/{comment_id}/mentions", response_model=list[CommentMentionRead])
def list_comment_mentions(
    comment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CommentMentionRead]:
    return MentionService(db).list_comment_mentions(comment_id=comment_id, current_user=current_user)


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(
    comment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    CommentService(db).delete_comment(comment_id=comment_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)