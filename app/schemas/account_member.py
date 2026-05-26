from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.account_member import AccountMemberRole


class AccountMemberRead(BaseModel):
    id: UUID
    account_id: UUID
    user_id: UUID
    role: AccountMemberRole
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)