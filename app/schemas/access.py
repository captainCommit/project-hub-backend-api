from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.access_grant import GrantRole, ProjectModule
from app.schemas.billing import BillingState


ScopeType = Literal["PORTFOLIO", "PROGRAM", "PROJECT"]


class GrantScope(BaseModel):
    """Exactly one of portfolio_id, program_id or project_id."""

    portfolio_id: UUID | None = None
    program_id: UUID | None = None
    project_id: UUID | None = None

    @model_validator(mode="after")
    def exactly_one_scope(self) -> "GrantScope":
        if sum(value is not None for value in (self.portfolio_id, self.program_id, self.project_id)) != 1:
            raise ValueError("Give exactly one of portfolio_id, program_id or project_id.")
        return self


def unique_modules(modules: list[ProjectModule] | None) -> list[ProjectModule] | None:
    return None if modules is None else list(dict.fromkeys(modules))


class AccessGrantCreate(GrantScope):
    user_id: UUID
    role: GrantRole
    # MEMBER grants: modules they may edit. Ignored for other roles.
    modules: list[ProjectModule] = Field(default_factory=list)
    # MEMBER and VIEWER grants: modules they may see; None means all. Edit modules are always visible.
    view_modules: list[ProjectModule] | None = None

    _unique_modules = field_validator("modules", "view_modules")(unique_modules)


class AccessGrantUpdate(BaseModel):
    role: GrantRole | None = None
    modules: list[ProjectModule] | None = None
    # Sending null makes every module visible; leaving it out keeps what the grant has.
    view_modules: list[ProjectModule] | None = None

    _unique_modules = field_validator("modules", "view_modules")(unique_modules)


class InviteAccess(GrantScope):
    """Where a newly invited person starts, at their invited role."""

    modules: list[ProjectModule] = Field(default_factory=list)
    view_modules: list[ProjectModule] | None = None

    _unique_modules = field_validator("modules", "view_modules")(unique_modules)


class AccessGrantUser(BaseModel):
    id: UUID
    email: str
    full_name: str | None
    account_role: str


class AccessGrantRead(BaseModel):
    id: UUID
    user: AccessGrantUser
    scope_type: ScopeType
    scope_id: UUID
    scope_name: str
    role: GrantRole
    modules: list[ProjectModule]
    # None means every module is visible.
    view_modules: list[ProjectModule] | None
    # True when the grant sits on a parent (portfolio or program) of the scope that was asked about.
    inherited: bool
    created_at: datetime


class PermissionRead(BaseModel):
    role: str
    can_manage: bool
    editable_modules: list[ProjectModule]
    visible_modules: list[ProjectModule]


class MyAccessRead(BaseModel):
    """The current user's effective permissions on everything they can see in an account."""

    account_role: str
    is_owner: bool
    billing: BillingState
    portfolios: dict[UUID, PermissionRead | None]
    programs: dict[UUID, PermissionRead | None]
    projects: dict[UUID, PermissionRead]
