from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams, paginate_statement, sort_descending
from app.models.attachment import Attachment


class AttachmentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **values: object) -> Attachment:
        attachment = Attachment(**values)
        self.db.add(attachment)
        self.db.flush()
        self.db.refresh(attachment)
        return attachment

    def get(self, attachment_id: UUID) -> Attachment | None:
        return self.db.get(Attachment, attachment_id)

    def list_for_entity_statement(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        sort: str = "-created_at",
    ) -> Select[tuple[Attachment]]:
        sort_column = Attachment.created_at.desc() if sort_descending(sort) else Attachment.created_at
        return select(Attachment).where(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id).order_by(
            sort_column,
            Attachment.id.desc(),
        )

    def list_for_entity(self, *, entity_type: str, entity_id: UUID, sort: str = "-created_at") -> list[Attachment]:
        statement = self.list_for_entity_statement(entity_type=entity_type, entity_id=entity_id, sort=sort)
        return list(self.db.scalars(statement).all())

    def list_for_entity_paginated(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        sort: str,
        pagination: PaginationParams,
    ) -> tuple[list[Attachment], int]:
        statement = self.list_for_entity_statement(entity_type=entity_type, entity_id=entity_id, sort=sort)
        items, total = paginate_statement(self.db, statement, pagination)
        return items, total

    def delete(self, attachment: Attachment) -> None:
        self.db.delete(attachment)
        self.db.flush()