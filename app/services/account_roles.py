"""Account > Users & Roles: the roles an account's members hold, and which one each member has.

A member's `role_id` decides what they can do (see app/services/access.py). Their `role` column is the seat that
billing counts, kept in step: OWNER for the Owner role, VIEWER for a role that can only view, MEMBER otherwise.
"""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account_member import AccountMember, AccountMemberRole
from app.models.account_role import ACTIONS, AREAS, AccountRole
from app.models.user import User
from app.schemas.account_roles import AccountRoleCreate, AccountRoleRead, AccountRoleUpdate
from app.services.access import LEGACY_ROLE_KEYS, access_for, forget_access, seat_of
from app.services.role_defaults import DEFAULT_ROLES, DEFAULT_ROLES_BY_KEY


# Seeding and assigning


def seed_roles(db: Session, account_id: UUID) -> dict[str, AccountRole]:
    """The account's built-in roles by key, creating any that are missing."""
    existing = {
        role.key: role
        for role in db.scalars(select(AccountRole).where(AccountRole.account_id == account_id, AccountRole.key.is_not(None)))
    }
    for order, definition in enumerate(DEFAULT_ROLES):
        key = str(definition["key"])
        if key in existing:
            continue
        role = AccountRole(
            account_id=account_id,
            key=key,
            name=str(definition["name"]),
            description=str(definition["description"]),
            permissions=clean_permissions(definition["permissions"]),  # type: ignore[arg-type]
            scope=str(definition["scope"]),
            sort_order=order,
            is_system=True,
        )
        db.add(role)
        existing[key] = role
    db.flush()
    return existing


def seat_for(role: AccountRole) -> AccountMemberRole:
    """Owner seat for the Owner role; a viewer seat for view-only roles; a full seat otherwise."""
    return AccountMemberRole(seat_of(role))


def role_for_seat(db: Session, account_id: UUID, seat: AccountMemberRole | str) -> AccountRole:
    """The built-in role standing in for an older seat role (OWNER, MANAGER, MEMBER, VIEWER)."""
    value = seat.value if isinstance(seat, AccountMemberRole) else seat
    return seed_roles(db, account_id)[LEGACY_ROLE_KEYS.get(value, "VIEWER")]


def assign_role(member: AccountMember, role: AccountRole) -> None:
    member.role_id = role.id
    member.role = seat_for(role).value


def clean_permissions(permissions: dict[str, list[str]]) -> dict[str, list[str]]:
    """Every area present, known actions only, in grid order. Any other right implies View."""
    cleaned: dict[str, list[str]] = {}
    for area in AREAS:
        wanted = {str(action).upper() for action in permissions.get(area, [])}
        if wanted:
            wanted.add("VIEW")
        cleaned[area] = [action for action in ACTIONS if action in wanted]
    return cleaned


# Owners managing roles


class AccountRoleService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_roles(self, *, account_id: UUID, current_user: User) -> list[AccountRoleRead]:
        access_for(self.db, account_id=account_id, user=current_user)
        seed_roles(self.db, account_id)
        self.db.commit()
        roles = self.db.scalars(
            select(AccountRole).where(AccountRole.account_id == account_id).order_by(AccountRole.sort_order, AccountRole.name)
        ).all()
        counts = dict(
            self.db.execute(
                select(AccountMember.role_id, func.count())
                .where(AccountMember.account_id == account_id)
                .group_by(AccountMember.role_id)
            ).all()
        )
        return [self.read(role, counts.get(role.id, 0)) for role in roles]

    def create_role(self, *, account_id: UUID, role_in: AccountRoleCreate, current_user: User) -> AccountRoleRead:
        self.require_owner(account_id, current_user)
        last = self.db.scalar(select(func.max(AccountRole.sort_order)).where(AccountRole.account_id == account_id)) or 0
        role = AccountRole(
            account_id=account_id,
            key=None,
            name=role_in.name.strip(),
            description=role_in.description,
            permissions=clean_permissions(role_in.permissions),
            scope=role_in.scope,
            sort_order=last + 1,
            is_system=False,
        )
        self.db.add(role)
        self.commit_unique_name()
        self.db.refresh(role)
        return self.read(role, 0)

    def update_role(self, *, role_id: UUID, role_in: AccountRoleUpdate, current_user: User) -> AccountRoleRead:
        role = self.get_or_404(role_id)
        self.require_owner(role.account_id, current_user)
        if role.key == "OWNER":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The Owner role always has full access.")
        changes = role_in.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"]:
            role.name = changes["name"].strip()
        if "description" in changes:
            role.description = changes["description"]
        if changes.get("permissions") is not None:
            role.permissions = clean_permissions(changes["permissions"])
        if changes.get("scope") is not None:
            role.scope = changes["scope"]
        self.resync_seats(role)
        self.commit_unique_name()
        self.db.refresh(role)
        return self.read(role, self.member_count(role))

    def reset_role(self, *, role_id: UUID, current_user: User) -> AccountRoleRead:
        """A built-in role back to how it shipped."""
        role = self.get_or_404(role_id)
        self.require_owner(role.account_id, current_user)
        definition = DEFAULT_ROLES_BY_KEY.get(role.key or "")
        if definition is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only built-in roles have defaults.")
        role.name = str(definition["name"])
        role.description = str(definition["description"])
        role.permissions = clean_permissions(definition["permissions"])  # type: ignore[arg-type]
        role.scope = str(definition["scope"])
        self.resync_seats(role)
        self.commit_unique_name()
        self.db.refresh(role)
        return self.read(role, self.member_count(role))

    def delete_role(self, *, role_id: UUID, current_user: User) -> None:
        role = self.get_or_404(role_id)
        self.require_owner(role.account_id, current_user)
        if role.is_system:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Built-in roles can't be deleted.")
        if self.member_count(role):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="People still have this role. Give them another role first.",
            )
        self.db.delete(role)
        self.db.commit()

    # Helpers

    def resync_seats(self, role: AccountRole) -> None:
        """Changing a role's rights can move its members between full and viewer seats."""
        seat = seat_for(role).value
        for member in self.db.scalars(select(AccountMember).where(AccountMember.role_id == role.id)):
            member.role = seat
        forget_access(self.db)

    def commit_unique_name(self) -> None:
        try:
            self.db.commit()
        except IntegrityError as error:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A role with that name already exists.") from error

    def member_count(self, role: AccountRole) -> int:
        return self.db.scalar(select(func.count()).select_from(AccountMember).where(AccountMember.role_id == role.id)) or 0

    def get_or_404(self, role_id: UUID) -> AccountRole:
        role = self.db.get(AccountRole, role_id)
        if role is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found.")
        return role

    def require_owner(self, account_id: UUID, current_user: User) -> None:
        scope = access_for(self.db, account_id=account_id, user=current_user)
        if not scope.is_owner:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only account owners can change roles.")
        scope.require_writable()

    def read(self, role: AccountRole, member_count: int) -> AccountRoleRead:
        definition = DEFAULT_ROLES_BY_KEY.get(role.key or "")
        return AccountRoleRead(
            id=role.id,
            key=role.key,
            name=role.name,
            description=role.description,
            permissions=clean_permissions(role.permissions or {}),
            scope=role.scope,
            is_system=role.is_system,
            editable=role.key != "OWNER",
            is_default=definition is not None
            and clean_permissions(role.permissions or {}) == clean_permissions(definition["permissions"])  # type: ignore[arg-type]
            and role.scope == definition["scope"],
            seat=seat_for(role).value,
            member_count=member_count,
        )
