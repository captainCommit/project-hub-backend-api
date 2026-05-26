from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.option_set import OptionSet
from app.models.option_value import OptionValue


class OptionSetRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        account_id: UUID,
        entity_type: str,
        name: str,
        description: str | None = None,
        is_system: bool = False,
    ) -> OptionSet:
        option_set = OptionSet(
            account_id=account_id,
            entity_type=entity_type,
            name=name,
            description=description,
            is_system=is_system,
        )
        self.db.add(option_set)
        self.db.flush()
        self.db.refresh(option_set)
        return option_set

    def get_by_id(self, option_set_id: UUID) -> OptionSet | None:
        return self.db.get(OptionSet, option_set_id)

    def list_for_account(self, account_id: UUID) -> list[OptionSet]:
        statement = (
            select(OptionSet)
            .where(OptionSet.account_id == account_id)
            .order_by(OptionSet.entity_type, OptionSet.name)
        )
        return list(self.db.scalars(statement).all())

    def list_for_account_filtered(
        self,
        *,
        account_id: UUID,
        entity_type: str | None = None,
        name: str | None = None,
    ) -> list[OptionSet]:
        statement = select(OptionSet).where(OptionSet.account_id == account_id)
        if entity_type is not None:
            statement = statement.where(OptionSet.entity_type == entity_type)
        if name is not None:
            statement = statement.where(OptionSet.name == name)
        statement = statement.order_by(OptionSet.entity_type, OptionSet.name)
        return list(self.db.scalars(statement).all())

    def update(
        self,
        option_set: OptionSet,
        *,
        entity_type: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> OptionSet:
        if entity_type is not None:
            option_set.entity_type = entity_type
        if name is not None:
            option_set.name = name
        if description is not None:
            option_set.description = description
        self.db.add(option_set)
        self.db.flush()
        self.db.refresh(option_set)
        return option_set


class OptionValueRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        option_set_id: UUID,
        label: str,
        value: str,
        color: str | None = None,
        sort_order: int = 0,
        is_default: bool = False,
    ) -> OptionValue:
        option_value = OptionValue(
            option_set_id=option_set_id,
            label=label,
            value=value,
            color=color,
            sort_order=sort_order,
            is_default=is_default,
        )
        self.db.add(option_value)
        self.db.flush()
        self.db.refresh(option_value)
        return option_value

    def get_by_id(self, option_value_id: UUID) -> OptionValue | None:
        return self.db.get(OptionValue, option_value_id)

    def list_for_option_set(
        self,
        option_set_id: UUID,
        *,
        include_inactive: bool = False,
    ) -> list[OptionValue]:
        statement = select(OptionValue).where(OptionValue.option_set_id == option_set_id)
        if not include_inactive:
            statement = statement.where(OptionValue.is_active.is_(True))
        statement = statement.order_by(OptionValue.sort_order, OptionValue.label)
        return list(self.db.scalars(statement).all())

    def unset_defaults_for_option_set(self, option_set_id: UUID) -> None:
        self.db.execute(
            update(OptionValue)
            .where(OptionValue.option_set_id == option_set_id)
            .values(is_default=False)
        )
        self.db.flush()

    def update(
        self,
        option_value: OptionValue,
        *,
        label: str | None = None,
        value: str | None = None,
        color: str | None = None,
        sort_order: int | None = None,
        is_active: bool | None = None,
        is_default: bool | None = None,
    ) -> OptionValue:
        if label is not None:
            option_value.label = label
        if value is not None:
            option_value.value = value
        if color is not None:
            option_value.color = color
        if sort_order is not None:
            option_value.sort_order = sort_order
        if is_active is not None:
            option_value.is_active = is_active
        if is_default is not None:
            option_value.is_default = is_default
        self.db.add(option_value)
        self.db.flush()
        self.db.refresh(option_value)
        return option_value