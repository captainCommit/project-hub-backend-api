from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaskRequiredSkill(Base):
    __tablename__ = "task_required_skills"
    __table_args__ = (
        CheckConstraint(
            "required_proficiency is null or required_proficiency in ('BEGINNER', 'INTERMEDIATE', 'ADVANCED', 'EXPERT')",
            name="ck_task_required_skills_required_proficiency",
        ),
        UniqueConstraint("task_id", "skill_id", name="uq_task_required_skills_task_id_skill_id"),
        Index("ix_task_required_skills_account_id_task_id", "account_id", "task_id"),
        Index("ix_task_required_skills_account_id_skill_id", "account_id", "skill_id"),
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
    skill_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    required_proficiency: Mapped[str | None] = mapped_column(String(50), nullable=True)
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