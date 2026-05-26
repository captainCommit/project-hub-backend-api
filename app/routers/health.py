from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.health_check import HealthStatusResponse


router = APIRouter(tags=["health"])


def is_database_connected(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


@router.get("/health", response_model=HealthStatusResponse)
def health_check(db: Session = Depends(get_db)) -> HealthStatusResponse:
    if is_database_connected(db):
        return HealthStatusResponse(status="ok", database="connected")

    return HealthStatusResponse(status="degraded", database="disconnected")