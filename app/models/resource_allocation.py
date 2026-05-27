from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ResourceAllocation(Base):
    __tablename__ = "resource_allocations"
    __table_args__ = (
        CheckConstraint(
            "allocated_hours is null or allocated_hours > 0",
            name="ck_resource_allocations_allocated_hours_positive",
        ),
        CheckConstraint(
            "start_date is null or end_date is null or end_date >= start_date",
            name="ck_resource_allocations_date_range",
        ),
        Index("ix_resource_allocations_account_id_resource_id", "account_id", "resource_id"),
        Index("ix_resource_allocations_account_id_task_id", "account_id", "task_id"),
        Index("ix_resource_allocations_start_date_end_date", "start_date", "end_date"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resources.id", ondelete="CASCADE"),
        nullable=False,
    )
    allocated_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
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