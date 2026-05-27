from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.schemas.search import SearchResponse
from app.services.auth import get_current_user
from app.services.search import SearchService


router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(min_length=2),
    entity_types: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, list[dict[str, object]]]:
    return SearchService(db).search(
        q=q,
        current_user=current_user,
        entity_types=entity_types,
        limit=limit,
    )