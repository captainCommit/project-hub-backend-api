from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OptionSetCreate(BaseModel):
    entity_type: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class OptionSetUpdate(BaseModel):
    entity_type: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class OptionSetRead(BaseModel):
    id: UUID
    account_id: UUID | None
    entity_type: str
    name: str
    description: str | None
    is_system: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OptionValueCreate(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    value: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=50)
    sort_order: int = 0
    is_default: bool = False


class OptionValueUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=255)
    value: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=50)
    sort_order: int | None = None
    is_active: bool | None = None
    is_default: bool | None = None


class OptionValueRead(BaseModel):
    id: UUID
    option_set_id: UUID
    label: str
    value: str
    color: str | None
    sort_order: int
    is_active: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OptionSetWithValuesRead(OptionSetRead):
    values: list[OptionValueRead]