from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class HealthCheckBase(BaseModel):
    service_name: str
    status: str


class HealthCheckCreate(HealthCheckBase):
    pass


class HealthCheckRead(HealthCheckBase):
    id: UUID
    checked_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HealthStatusResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["connected", "disconnected"]