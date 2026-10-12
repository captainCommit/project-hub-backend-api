from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ActivityChangeRead(BaseModel):
    field: str
    # Readable field name, e.g. "Due date" for finish_date.
    label: str
    # Readable values: IDs become names, dates and percentages are formatted. None means empty.
    old: str | None
    new: str | None


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
    actor_name: str | None = None
    entity_name: str | None = None
    changes: list[ActivityChangeRead] = []

    model_config = ConfigDict(from_attributes=True)