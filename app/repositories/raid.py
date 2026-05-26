from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginate_statement, sort_descending
from app.models.decision_option import DecisionOption
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue


class RaidRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_item(self, model_cls: type[Any], **values: object) -> Any:
        item = model_cls(**values)
        self.db.add(item)
        self.db.flush()
        self.db.refresh(item)
        return item

    def get_item(self, model_cls: type[Any], item_id: UUID) -> Any | None:
        return self.db.get(model_cls, item_id)

    def list_items_for_project(
        self,
        model_cls: type[Any],
        *,
        project_id: UUID,
        number_field: str,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        sort: str | None = None,
    ) -> list[Any]:
        statement = self.list_items_for_project_statement(
            model_cls,
            project_id=project_id,
            number_field=number_field,
            status_id=status_id,
            priority_id=priority_id,
            sort=sort,
        )
        return list(self.db.scalars(statement).all())

    def list_items_for_project_paginated(
        self,
        model_cls: type[Any],
        *,
        project_id: UUID,
        number_field: str,
        pagination: PaginationParams,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        sort: str | None = None,
    ) -> tuple[list[Any], int]:
        statement = self.list_items_for_project_statement(
            model_cls,
            project_id=project_id,
            number_field=number_field,
            status_id=status_id,
            priority_id=priority_id,
            sort=sort,
        )
        items, total = paginate_statement(self.db, statement, pagination)
        return items, total

    def list_items_for_project_statement(
        self,
        model_cls: type[Any],
        *,
        project_id: UUID,
        number_field: str,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        sort: str | None = None,
    ) -> Select[Any]:
        statement = (
            select(model_cls)
            .where(model_cls.project_id == project_id)
        )
        if status_id is not None and hasattr(model_cls, "status_id"):
            statement = statement.where(model_cls.status_id == status_id)
        if priority_id is not None and hasattr(model_cls, "priority_id"):
            statement = statement.where(model_cls.priority_id == priority_id)

        sort_value = sort or number_field
        sort_field = sort_value.removeprefix("-")
        sort_column = getattr(model_cls, sort_field)
        if sort_descending(sort_value):
            sort_column = sort_column.desc()
        return statement.order_by(sort_column, getattr(model_cls, number_field), model_cls.id)

    def update_item(self, item: Any, changes: dict[str, object]) -> Any:
        for field, value in changes.items():
            setattr(item, field, value)
        self.db.add(item)
        self.db.flush()
        self.db.refresh(item)
        return item

    def list_numbers_for_project(
        self,
        model_cls: type[Any],
        *,
        project_id: UUID,
        number_field: str,
    ) -> list[str]:
        number_column = getattr(model_cls, number_field)
        statement = select(number_column).where(model_cls.project_id == project_id)
        return [str(number) for number in self.db.scalars(statement).all()]

    def get_valid_option(
        self,
        *,
        account_id: UUID,
        entity_type: str,
        option_name: str,
        option_value_id: UUID,
    ) -> OptionValue | None:
        statement = (
            select(OptionValue)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionValue.id == option_value_id,
                OptionSet.account_id == account_id,
                OptionSet.entity_type == entity_type,
                OptionSet.name == option_name,
                OptionValue.is_active.is_(True),
            )
        )
        return self.db.scalar(statement)

    def get_default_status_id(self, *, account_id: UUID, entity_type: str) -> UUID | None:
        statement = (
            select(OptionValue.id)
            .join(OptionSet, OptionSet.id == OptionValue.option_set_id)
            .where(
                OptionSet.account_id == account_id,
                OptionSet.entity_type == entity_type,
                OptionSet.name == "STATUS",
                OptionValue.is_active.is_(True),
                OptionValue.is_default.is_(True),
            )
            .order_by(OptionValue.sort_order, OptionValue.label)
            .limit(1)
        )
        return self.db.scalar(statement)

    def get_option_values_by_ids(self, option_value_ids: Iterable[UUID]) -> dict[UUID, OptionValue]:
        option_value_ids = list(option_value_ids)
        if not option_value_ids:
            return {}
        statement = select(OptionValue).where(OptionValue.id.in_(option_value_ids))
        return {option_value.id: option_value for option_value in self.db.scalars(statement).all()}

    def create_decision_option(self, **values: object) -> DecisionOption:
        option = DecisionOption(**values)
        self.db.add(option)
        self.db.flush()
        self.db.refresh(option)
        return option

    def get_decision_option(self, option_id: UUID) -> DecisionOption | None:
        return self.db.get(DecisionOption, option_id)

    def list_decision_options(self, decision_id: UUID) -> list[DecisionOption]:
        statement = (
            select(DecisionOption)
            .where(DecisionOption.decision_id == decision_id)
            .order_by(DecisionOption.sort_order, DecisionOption.created_at)
        )
        return list(self.db.scalars(statement).all())

    def update_decision_option(
        self,
        option: DecisionOption,
        changes: dict[str, object],
    ) -> DecisionOption:
        for field, value in changes.items():
            setattr(option, field, value)
        self.db.add(option)
        self.db.flush()
        self.db.refresh(option)
        return option

    def delete_decision_option(self, option: DecisionOption) -> None:
        self.db.delete(option)
        self.db.flush()