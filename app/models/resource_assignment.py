from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ResourceAssignment(Base):
    """A portfolio, program or project a resource is assigned to (Resources > Capacity > Add Resource).

    A project assignment also gives the resource's login access to that project (see app.services.access).
    Portfolio and program assignments record where the person works; they don't open anything up by themselves.
    """

    __tablename__ = "resource_assignments"
    __table_args__ = (
        CheckConstraint(
            "(case when portfolio_id is null then 0 else 1 end)"
            " + (case when program_id is null then 0 else 1 end)"
            " + (case when project_id is null then 0 else 1 end) = 1",
            name="ck_resource_assignments_one_target",
        ),
        UniqueConstraint("resource_id", "portfolio_id", name="uq_resource_assignments_resource_id_portfolio_id"),
        UniqueConstraint("resource_id", "program_id", name="uq_resource_assignments_resource_id_program_id"),
        UniqueConstraint("resource_id", "project_id", name="uq_resource_assignments_resource_id_project_id"),
        Index("ix_resource_assignments_account_id", "account_id"),
        Index("ix_resource_assignments_project_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    portfolio_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=True
    )
    program_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("programs.id", ondelete="CASCADE"), nullable=True
    )
    project_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
