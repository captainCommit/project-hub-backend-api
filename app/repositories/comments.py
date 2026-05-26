from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginate_statement, sort_descending
from app.models.comment import Comment


class CommentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **values: object) -> Comment:
        comment = Comment(**values)
        self.db.add(comment)
        self.db.flush()
        self.db.refresh(comment)
        return comment

    def get(self, comment_id: UUID) -> Comment | None:
        return self.db.get(Comment, comment_id)

    def list_for_entity_statement(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        sort: str = "created_at",
    ) -> Select[tuple[Comment]]:
        sort_column = Comment.created_at
        if sort_descending(sort):
            sort_column = sort_column.desc()
        return (
            select(Comment)
            .where(Comment.entity_type == entity_type, Comment.entity_id == entity_id)
            .order_by(sort_column, Comment.id)
        )

    def list_for_entity(self, *, entity_type: str, entity_id: UUID, sort: str = "created_at") -> list[Comment]:
        statement = self.list_for_entity_statement(entity_type=entity_type, entity_id=entity_id, sort=sort)
        return list(self.db.scalars(statement).all())

    def list_for_entity_paginated(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        sort: str,
        pagination: PaginationParams,
    ) -> tuple[list[Comment], int]:
        statement = (
            self.list_for_entity_statement(entity_type=entity_type, entity_id=entity_id, sort=sort)
        )
        items, total = paginate_statement(self.db, statement, pagination)
        return items, total

    def update(self, comment: Comment, *, body: str) -> Comment:
        comment.body = body
        self.db.add(comment)
        self.db.flush()
        self.db.refresh(comment)
        return comment

    def delete(self, comment: Comment) -> None:
        self.db.delete(comment)
        self.db.flush()