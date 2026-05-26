import re
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account_member import AccountMemberRole
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.user import User
from app.repositories.account_members import AccountMemberRepository
from app.repositories.accounts import AccountRepository
from app.repositories.options import OptionSetRepository, OptionValueRepository
from app.schemas.options import OptionSetCreate, OptionSetUpdate, OptionValueCreate, OptionValueUpdate
from app.services.option_defaults import DEFAULT_OPTION_SETS


OPTION_WRITE_ROLES = {AccountMemberRole.OWNER.value, AccountMemberRole.ADMIN.value}


def normalize_key(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_")
    return normalized.upper()


class OptionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.account_members = AccountMemberRepository(db)
        self.option_sets = OptionSetRepository(db)
        self.option_values = OptionValueRepository(db)

    def seed_defaults_for_account(self, account_id: UUID) -> None:
        for option_set_definition in DEFAULT_OPTION_SETS:
            option_set = self.option_sets.create(
                account_id=account_id,
                entity_type=str(option_set_definition["entity_type"]),
                name=str(option_set_definition["name"]),
                is_system=True,
            )
            values = tuple(option_set_definition["values"])  # type: ignore[arg-type]
            for sort_order, label in enumerate(values):
                self.option_values.create(
                    option_set_id=option_set.id,
                    label=str(label),
                    value=normalize_key(str(label)),
                    sort_order=sort_order,
                    is_default=sort_order == 0,
                )

    def list_option_sets(self, *, account_id: UUID, current_user: User) -> list[OptionSet]:
        self.require_account_member(account_id=account_id, user_id=current_user.id)
        return self.option_sets.list_for_account(account_id)

    def create_option_set(
        self,
        *,
        account_id: UUID,
        option_set_in: OptionSetCreate,
        current_user: User,
    ) -> OptionSet:
        self.require_account_role(
            account_id=account_id,
            user_id=current_user.id,
            allowed_roles=OPTION_WRITE_ROLES,
        )
        try:
            option_set = self.option_sets.create(
                account_id=account_id,
                entity_type=normalize_key(option_set_in.entity_type),
                name=normalize_key(option_set_in.name),
                description=option_set_in.description,
            )
            self.db.commit()
            self.db.refresh(option_set)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Option set already exists for this account, entity type, and name.",
            ) from exc
        return option_set

    def update_option_set(
        self,
        *,
        option_set_id: UUID,
        option_set_in: OptionSetUpdate,
        current_user: User,
    ) -> OptionSet:
        option_set = self.get_option_set_or_404(option_set_id)
        account_id = self.require_account_scoped_option_set(option_set)
        self.require_account_role(
            account_id=account_id,
            user_id=current_user.id,
            allowed_roles=OPTION_WRITE_ROLES,
        )
        try:
            option_set = self.option_sets.update(
                option_set,
                entity_type=normalize_key(option_set_in.entity_type) if option_set_in.entity_type else None,
                name=normalize_key(option_set_in.name) if option_set_in.name else None,
                description=option_set_in.description,
            )
            self.db.commit()
            self.db.refresh(option_set)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Option set already exists for this account, entity type, and name.",
            ) from exc
        return option_set

    def list_options(
        self,
        *,
        account_id: UUID,
        current_user: User,
        entity_type: str | None = None,
        name: str | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, object]]:
        if include_inactive:
            self.require_account_role(
                account_id=account_id,
                user_id=current_user.id,
                allowed_roles=OPTION_WRITE_ROLES,
            )
        else:
            self.require_account_member(account_id=account_id, user_id=current_user.id)

        option_sets = self.option_sets.list_for_account_filtered(
            account_id=account_id,
            entity_type=normalize_key(entity_type) if entity_type else None,
            name=normalize_key(name) if name else None,
        )
        return [
            {
                **option_set.__dict__,
                "values": self.option_values.list_for_option_set(
                    option_set.id,
                    include_inactive=include_inactive,
                ),
            }
            for option_set in option_sets
        ]

    def list_option_values(
        self,
        *,
        option_set_id: UUID,
        current_user: User,
        include_inactive: bool = False,
    ) -> list[OptionValue]:
        option_set = self.get_option_set_or_404(option_set_id)
        account_id = self.require_account_scoped_option_set(option_set)
        if include_inactive:
            self.require_account_role(
                account_id=account_id,
                user_id=current_user.id,
                allowed_roles=OPTION_WRITE_ROLES,
            )
        else:
            self.require_account_member(account_id=account_id, user_id=current_user.id)
        return self.option_values.list_for_option_set(
            option_set_id,
            include_inactive=include_inactive,
        )

    def create_option_value(
        self,
        *,
        option_set_id: UUID,
        option_value_in: OptionValueCreate,
        current_user: User,
    ) -> OptionValue:
        option_set = self.get_option_set_or_404(option_set_id)
        account_id = self.require_account_scoped_option_set(option_set)
        self.require_account_role(
            account_id=account_id,
            user_id=current_user.id,
            allowed_roles=OPTION_WRITE_ROLES,
        )

        value = normalize_key(option_value_in.value or option_value_in.label)
        try:
            if option_value_in.is_default:
                self.option_values.unset_defaults_for_option_set(option_set_id)
            option_value = self.option_values.create(
                option_set_id=option_set_id,
                label=option_value_in.label,
                value=value,
                color=option_value_in.color,
                sort_order=option_value_in.sort_order,
                is_default=option_value_in.is_default,
            )
            self.db.commit()
            self.db.refresh(option_value)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Option value already exists in this option set.",
            ) from exc
        return option_value

    def update_option_value(
        self,
        *,
        option_value_id: UUID,
        option_value_in: OptionValueUpdate,
        current_user: User,
    ) -> OptionValue:
        option_value = self.get_option_value_or_404(option_value_id)
        option_set = self.get_option_set_or_404(option_value.option_set_id)
        account_id = self.require_account_scoped_option_set(option_set)
        self.require_account_role(
            account_id=account_id,
            user_id=current_user.id,
            allowed_roles=OPTION_WRITE_ROLES,
        )

        value = normalize_key(option_value_in.value) if option_value_in.value else None
        try:
            if option_value_in.is_default is True:
                self.option_values.unset_defaults_for_option_set(option_set.id)
            option_value = self.option_values.update(
                option_value,
                label=option_value_in.label,
                value=value,
                color=option_value_in.color,
                sort_order=option_value_in.sort_order,
                is_active=option_value_in.is_active,
                is_default=option_value_in.is_default,
            )
            self.db.commit()
            self.db.refresh(option_value)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Option value already exists in this option set.",
            ) from exc
        return option_value

    def deactivate_option_value(self, *, option_value_id: UUID, current_user: User) -> OptionValue:
        return self.update_option_value(
            option_value_id=option_value_id,
            option_value_in=OptionValueUpdate(is_active=False),
            current_user=current_user,
        )

    def get_option_set_or_404(self, option_set_id: UUID) -> OptionSet:
        option_set = self.option_sets.get_by_id(option_set_id)
        if option_set is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Option set not found.")
        return option_set

    def get_option_value_or_404(self, option_value_id: UUID) -> OptionValue:
        option_value = self.option_values.get_by_id(option_value_id)
        if option_value is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Option value not found.")
        return option_value

    def require_account_scoped_option_set(self, option_set: OptionSet) -> UUID:
        if option_set.account_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="System-level option sets are not editable in Phase 2.",
            )
        return option_set.account_id

    def require_account_member(self, *, account_id: UUID, user_id: UUID) -> None:
        account = self.accounts.get_by_id(account_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access denied.")

    def require_account_role(
        self,
        *,
        account_id: UUID,
        user_id: UUID,
        allowed_roles: set[str],
    ) -> None:
        self.require_account_member(account_id=account_id, user_id=user_id)
        membership = self.account_members.get_for_user(account_id=account_id, user_id=user_id)
        if membership is None or membership.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient account role.")