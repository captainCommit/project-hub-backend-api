from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Dependency(Base):
    __tablename__ = "dependencies"
    __table_args__ = (
        UniqueConstraint("project_id", "dependency_number", name="uq_dependencies_project_id_dependency_number"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    program_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("programs.id", ondelete="SET NULL"),
        nullable=True,
    )
    dependency_number: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("option_values.id", ondelete="SET NULL"),
        nullable=True,
    )
    status_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("option_values.id", ondelete="SET NULL"),
        nullable=True,
    )
    impact_of_delay: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entered_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date_entered: Mapped[date | None] = mapped_column(Date, nullable=True)
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
