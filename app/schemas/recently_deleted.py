from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class DeletedItemRead(BaseModel):
    id: UUID
    entity_type: Literal["PORTFOLIO", "PROGRAM", "PROJECT"]
    entity_id: UUID
    name: str
    description: str | None = None
    # "Portfolio" for a program, "Portfolio / Program" for a project.
    location: str | None = None
    # Projects removed with it (a portfolio or program counts its projects; a project counts itself).
    project_count: int
    deleted_at: datetime
    deleted_by_id: UUID | None = None
    deleted_by_name: str | None = None
    # When it is permanently deleted.
    expires_at: datetime
    can_restore: bool


class RestoredItemRead(BaseModel):
    entity_type: Literal["PORTFOLIO", "PROGRAM", "PROJECT"]
    entity_id: UUID
    name: str
