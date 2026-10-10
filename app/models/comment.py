from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from app.core.database import Base
from app.models.attachment import Attachment
from app.models.user import User


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    parent_comment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    author: Mapped[User | None] = relationship(foreign_keys=[created_by], lazy="selectin", viewonly=True)
    attachments: Mapped[list[Attachment]] = relationship(
        primaryjoin=lambda: (Attachment.entity_type == "COMMENT") & (foreign(Attachment.entity_id) == Comment.id),
        order_by=lambda: Attachment.created_at,
        lazy="selectin",
        viewonly=True,
    )

    @property
    def created_by_name(self) -> str | None:
        if self.author is None:
            return None
        return self.author.full_name or self.author.email
