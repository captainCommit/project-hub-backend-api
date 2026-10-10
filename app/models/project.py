from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from enum import Enum

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProjectDeliveryType(str, Enum):
    WATERFALL = "WATERFALL"
    AGILE = "AGILE"
    HYBRID = "HYBRID"


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "delivery_type in ('WATERFALL', 'AGILE', 'HYBRID')",
            name="ck_projects_delivery_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    portfolio_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
    )
    program_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ProjectDeliveryType.WATERFALL.value,
        server_default=ProjectDeliveryType.WATERFALL.value,
    )
    status_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("option_values.id", ondelete="SET NULL"),
        nullable=True,
    )
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    target_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    budget: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
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