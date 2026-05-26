from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

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

    def list_for_entity(self, *, entity_type: str, entity_id: UUID) -> list[Comment]:
        statement = (
            select(Comment)
            .where(Comment.entity_type == entity_type, Comment.entity_id == entity_id)
            .order_by(Comment.created_at, Comment.id)
        )
        return list(self.db.scalars(statement).all())

    def update(self, comment: Comment, *, body: str) -> Comment:
        comment.body = body
        self.db.add(comment)
        self.db.flush()
        self.db.refresh(comment)
        return comment

    def delete(self, comment: Comment) -> None:
        self.db.delete(comment)
        self.db.flush()