from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TestCase(Base):
    __tablename__ = "test_cases"
    __table_args__ = (
        UniqueConstraint("project_id", "test_case_number", name="uq_test_cases_project_id_test_case_number"),
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
    test_case_number: Mapped[str] = mapped_column(String(50), nullable=False)
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
    sprint_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sprints.id", ondelete="SET NULL"),
        nullable=True,
    )
    requirement_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("requirements.id", ondelete="SET NULL"),
        nullable=True,
    )
    designed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    executed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    execution_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    result: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="NOT_EXECUTED",
        server_default="NOT_EXECUTED",
    )
    dependencies: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_results: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_results: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
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
