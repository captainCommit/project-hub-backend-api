from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DeletedRecord(Base):
    """A deleted portfolio, program or project, kept so it can be restored (Account > Recently deleted).

    The rows themselves are removed as before; `snapshot` holds a copy of every row the delete removed
    (see services/recycle_bin.py) so a restore can put them back with their original ids.
    """

    __tablename__ = "deleted_records"
    __table_args__ = (Index("ix_deleted_records_account_id_deleted_at", "account_id", "deleted_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # PORTFOLIO, PROGRAM or PROJECT
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Where it lived, for the table: the program and/or portfolio name.
    location: Mapped[str | None] = mapped_column(String(600), nullable=True)
    project_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    deleted_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
