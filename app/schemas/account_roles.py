from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.account_role import ACTIONS, AREAS

Scope = Literal["ASSIGNED", "ALL"]


def check_permissions(permissions: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
    """{area: [action, ...]} with known areas and actions only."""
    if permissions is None:
        return None
    for area, actions in permissions.items():
        if area not in AREAS:
            raise ValueError(f"Unknown area: {area}")
        unknown = {str(action).upper() for action in actions} - set(ACTIONS)
        if unknown:
            raise ValueError(f"Unknown actions for {area}: {', '.join(sorted(unknown))}")
    return permissions


class AccountRoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permissions: dict[str, list[str]] = Field(default_factory=dict)
    scope: Scope = "ASSIGNED"

    _check = field_validator("permissions")(check_permissions)


class AccountRoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permissions: dict[str, list[str]] | None = None
    scope: Scope | None = None

    _check = field_validator("permissions")(check_permissions)


class AccountRoleRead(BaseModel):
    id: UUID
    # Built-in roles: OWNER, PORTFOLIO_MANAGER, PROGRAM_MANAGER, PROJECT_MANAGER, CONTRIBUTOR, VIEWER. None for custom.
    key: str | None
    name: str
    description: str | None
    permissions: dict[str, list[str]]
    scope: Scope
    is_system: bool
    # False for Owner, which always has everything.
    editable: bool
    # A built-in role still as it shipped (Reset to default has nothing to do).
    is_default: bool
    # The seat it costs: OWNER, MEMBER (full) or VIEWER.
    seat: str
    member_count: int
