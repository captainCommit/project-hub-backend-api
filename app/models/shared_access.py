from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SharedAccess(Base):
    __tablename__ = "shared_access"
    __table_args__ = (
        UniqueConstraint("item_type", "item_id", "shared_with_email", name="uq_shared_access_item_email"),
        CheckConstraint("item_type in ('PORTFOLIO', 'PROGRAM', 'PROJECT')", name="ck_shared_access_item_type"),
        CheckConstraint("access_level in ('VIEW', 'EDIT')", name="ck_shared_access_access_level"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)
    item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    shared_with_email: Mapped[str] = mapped_column(String(255), nullable=False)
    access_level: Mapped[str] = mapped_column(String(10), nullable=False, default="VIEW", server_default="VIEW")
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
