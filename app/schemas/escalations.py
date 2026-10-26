from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class EscalationCreate(BaseModel):
    """Who to escalate to: one person (a resource), a team (a resource department), or both."""

    resource_id: UUID | None = None
    team: str | None = Field(default=None, min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def needs_someone(self) -> "EscalationCreate":
        if self.resource_id is None and not (self.team or "").strip():
            raise ValueError("Choose a person or a team to escalate to.")
        return self


class EscalationRecipient(BaseModel):
    name: str
    # False when the account can't send email yet; they still see it in the app.
    emailed: bool


class EscalationSkipped(BaseModel):
    name: str
    reason: str


class EscalationRead(BaseModel):
    escalated_to: str
    notified: list[EscalationRecipient]
    skipped: list[EscalationSkipped]
