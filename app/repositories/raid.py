from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginate_statement, sort_descending
from app.models.decision_option import DecisionOption
from app.models.option_set import OptionSet
from app.models.option_value import OptionValue
from app.models.program import Program
from app.models.project import Project


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

    def list_items_for_account(
        self,
        model_cls: type[Any],
        *,
        account_id: UUID,
        sort: str,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        assigned_to: UUID | None = None,
        search: str | None = None,
        search_fields: Iterable[str] = (),
    ) -> list[Any]:
        statement = self.list_items_for_account_statement(
            model_cls,
            account_id=account_id,
            sort=sort,
            project_id=project_id,
            program_id=program_id,
            status_id=status_id,
            priority_id=priority_id,
            assigned_to=assigned_to,
            search=search,
            search_fields=search_fields,
        )
        return list(self.db.scalars(statement).all())

    def list_items_for_account_paginated(
        self,
        model_cls: type[Any],
        *,
        account_id: UUID,
        sort: str,
        pagination: PaginationParams,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        assigned_to: UUID | None = None,
        search: str | None = None,
        search_fields: Iterable[str] = (),
    ) -> tuple[list[Any], int]:
        statement = self.list_items_for_account_statement(
            model_cls,
            account_id=account_id,
            sort=sort,
            project_id=project_id,
            program_id=program_id,
            status_id=status_id,
            priority_id=priority_id,
            assigned_to=assigned_to,
            search=search,
            search_fields=search_fields,
        )
        items, total = paginate_statement(self.db, statement, pagination)
        return items, total

    def list_items_for_account_statement(
        self,
        model_cls: type[Any],
        *,
        account_id: UUID,
        sort: str,
        project_id: UUID | None = None,
        program_id: UUID | None = None,
        status_id: UUID | None = None,
        priority_id: UUID | None = None,
        assigned_to: UUID | None = None,
        search: str | None = None,
        search_fields: Iterable[str] = (),
    ) -> Select[Any]:
        statement = select(model_cls).where(model_cls.account_id == account_id)
        if project_id is not None:
            statement = statement.where(model_cls.project_id == project_id)
        if program_id is not None and hasattr(model_cls, "program_id"):
            statement = statement.where(model_cls.program_id == program_id)
        if status_id is not None and hasattr(model_cls, "status_id"):
            statement = statement.where(model_cls.status_id == status_id)
        if priority_id is not None and hasattr(model_cls, "priority_id"):
            statement = statement.where(model_cls.priority_id == priority_id)
        if assigned_to is not None and hasattr(model_cls, "assigned_to"):
            statement = statement.where(model_cls.assigned_to == assigned_to)

        search_value = (search or "").strip()
        if search_value:
            search_clauses = [getattr(model_cls, field).ilike(f"%{search_value}%") for field in search_fields]
            if search_clauses:
                statement = statement.where(or_(*search_clauses))

        sort_field = sort.removeprefix("-")
        sort_column = getattr(model_cls, sort_field)
        if sort_descending(sort):
            sort_column = sort_column.desc()
        return statement.order_by(sort_column, model_cls.id)

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

    def get_projects_by_ids(self, project_ids: Iterable[UUID]) -> dict[UUID, Project]:
        project_ids = list(project_ids)
        if not project_ids:
            return {}
        statement = select(Project).where(Project.id.in_(project_ids))
        return {project.id: project for project in self.db.scalars(statement).all()}

    def get_programs_by_ids(self, program_ids: Iterable[UUID]) -> dict[UUID, Program]:
        program_ids = list(program_ids)
        if not program_ids:
            return {}
        statement = select(Program).where(Program.id.in_(program_ids))
        return {program.id: program for program in self.db.scalars(statement).all()}

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