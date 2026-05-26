from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ActivityLogRead(BaseModel):
    id: UUID
    account_id: UUID
    entity_type: str
    entity_id: UUID
    action: str
    old_values: dict[str, object] | None
    new_values: dict[str, object] | None
    created_by: UUID | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)