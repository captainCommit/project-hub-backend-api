from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AccountUserRead(BaseModel):
    id: UUID
    email: str
    full_name: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)