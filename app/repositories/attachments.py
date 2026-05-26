from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

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

    def list_for_entity(self, *, entity_type: str, entity_id: UUID) -> list[Attachment]:
        statement = (
            select(Attachment)
            .where(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id)
            .order_by(Attachment.created_at.desc(), Attachment.id.desc())
        )
        return list(self.db.scalars(statement).all())

    def delete(self, attachment: Attachment) -> None:
        self.db.delete(attachment)
        self.db.flush()