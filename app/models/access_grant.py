from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GrantRole(str, Enum):
    MANAGER = "MANAGER"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"


class ProjectModule(str, Enum):
    """Parts of a project a MEMBER grant can allow editing, one toggle each."""

    TASKS = "TASKS"
    SPRINTS = "SPRINTS"
    RISKS = "RISKS"
    ISSUES = "ISSUES"
    ASSUMPTIONS = "ASSUMPTIONS"
    DECISIONS = "DECISIONS"
    DEPENDENCIES = "DEPENDENCIES"
    CHANGE_REQUESTS = "CHANGE_REQUESTS"
    REQUIREMENTS = "REQUIREMENTS"
    TESTING = "TESTING"
    FINANCIALS = "FINANCIALS"


ALL_MODULES = [module.value for module in ProjectModule]

ONE_SCOPE_SQL = (
    "(CASE WHEN portfolio_id IS NULL THEN 0 ELSE 1 END"
    " + CASE WHEN program_id IS NULL THEN 0 ELSE 1 END"
    " + CASE WHEN project_id IS NULL THEN 0 ELSE 1 END) = 1"
)


class AccessGrant(Base):
    """A user's role on one portfolio, program or project, covering everything beneath it.

    Exactly one of portfolio_id / program_id / project_id is set. Owners need no grants.
    """

    __tablename__ = "access_grants"
    __table_args__ = (
        CheckConstraint("role in ('MANAGER', 'MEMBER', 'VIEWER')", name="ck_access_grants_role"),
        CheckConstraint(ONE_SCOPE_SQL, name="ck_access_grants_one_scope"),
        UniqueConstraint("user_id", "portfolio_id", name="uq_access_grants_user_portfolio"),
        UniqueConstraint("user_id", "program_id", name="uq_access_grants_user_program"),
        UniqueConstraint("user_id", "project_id", name="uq_access_grants_user_project"),
        Index("ix_access_grants_account_id_user_id", "account_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    portfolio_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=True,
    )
    program_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=True,
    )
    project_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # MEMBER grants: the modules this person may edit. Ignored for MANAGER (all) and VIEWER (none).
    modules: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # MEMBER and VIEWER grants: the modules this person may see. None means all. Ignored for MANAGER (all).
    view_modules: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
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
