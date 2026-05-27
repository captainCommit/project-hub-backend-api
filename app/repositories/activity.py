from uuid import UUID

from sqlalchemy import Select, and_, case, or_, select
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginate_statement, sort_descending
from app.models.activity_log import ActivityLog
from app.models.assumption import Assumption
from app.models.decision import Decision
from app.models.decision_option import DecisionOption
from app.models.issue import Issue
from app.models.risk import Risk
from app.models.task import Task


class ActivityLogRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **values: object) -> ActivityLog:
        activity = ActivityLog(**values)
        self.db.add(activity)
        self.db.flush()
        self.db.refresh(activity)
        return activity

    def list_for_entity_statement(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        sort: str = "-created_at",
    ) -> Select[tuple[ActivityLog]]:
        descending = sort_descending(sort)
        sort_column = ActivityLog.created_at.desc() if descending else ActivityLog.created_at
        created_tie_breaker = case((ActivityLog.action == "CREATED", 1 if descending else 0), else_=0 if descending else 1)
        return select(ActivityLog).where(ActivityLog.entity_type == entity_type, ActivityLog.entity_id == entity_id).order_by(
            sort_column,
            created_tie_breaker,
            ActivityLog.id.desc(),
        )

    def list_for_entity(self, *, entity_type: str, entity_id: UUID, sort: str = "-created_at") -> list[ActivityLog]:
        statement = self.list_for_entity_statement(entity_type=entity_type, entity_id=entity_id, sort=sort)
        return list(self.db.scalars(statement).all())

    def list_for_entity_paginated(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        sort: str,
        pagination: PaginationParams,
    ) -> tuple[list[ActivityLog], int]:
        statement = self.list_for_entity_statement(entity_type=entity_type, entity_id=entity_id, sort=sort)
        items, total = paginate_statement(self.db, statement, pagination)
        return items, total

    def list_for_project_statement(self, project_id: UUID, *, sort: str = "-created_at") -> Select[tuple[ActivityLog]]:
        conditions = [and_(ActivityLog.entity_type == "PROJECT", ActivityLog.entity_id == project_id)]
        for entity_type, ids in (
            ("TASK", self._ids_for_project(Task, project_id)),
            ("RISK", self._ids_for_project(Risk, project_id)),
            ("ISSUE", self._ids_for_project(Issue, project_id)),
            ("ASSUMPTION", self._ids_for_project(Assumption, project_id)),
            ("DECISION", self._ids_for_project(Decision, project_id)),
        ):
            if ids:
                conditions.append(and_(ActivityLog.entity_type == entity_type, ActivityLog.entity_id.in_(ids)))

        decision_ids = self._ids_for_project(Decision, project_id)
        if decision_ids:
            decision_option_statement = select(DecisionOption.id).where(DecisionOption.decision_id.in_(decision_ids))
            decision_option_ids = list(self.db.scalars(decision_option_statement).all())
            if decision_option_ids:
                conditions.append(
                    and_(
                        ActivityLog.entity_type == "DECISION_OPTION",
                        ActivityLog.entity_id.in_(decision_option_ids),
                    )
                )

        descending = sort_descending(sort)
        sort_column = ActivityLog.created_at.desc() if descending else ActivityLog.created_at
        created_tie_breaker = case((ActivityLog.action == "CREATED", 1 if descending else 0), else_=0 if descending else 1)
        return select(ActivityLog).where(or_(*conditions)).order_by(sort_column, created_tie_breaker, ActivityLog.id.desc())

    def list_for_project(self, project_id: UUID, *, sort: str = "-created_at") -> list[ActivityLog]:
        statement = self.list_for_project_statement(project_id, sort=sort)
        return list(self.db.scalars(statement).all())

    def list_for_project_paginated(
        self,
        project_id: UUID,
        *,
        sort: str,
        pagination: PaginationParams,
    ) -> tuple[list[ActivityLog], int]:
        statement = self.list_for_project_statement(project_id, sort=sort)
        items, total = paginate_statement(self.db, statement, pagination)
        return items, total

    def _ids_for_project(self, model_cls: type[object], project_id: UUID) -> list[UUID]:
        statement = select(model_cls.id).where(model_cls.project_id == project_id)  # type: ignore[attr-defined]
        return list(self.db.scalars(statement).all())