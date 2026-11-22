from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


# The parts of Row a role grants rights over (Account > Users & Roles), and what it may do in each.
AREAS = (
    "PORTFOLIOS",
    "PROGRAMS",
    "PROJECTS",
    "REQUIREMENTS",
    "TESTING",
    "SPRINTS",
    "RAID",
    "GOVERNANCE",
    "RESOURCES",
    "REPORTING",
)
ACTIONS = ("VIEW", "CREATE", "EDIT", "DELETE", "APPROVE")

# Which projects a role reaches: those the person is assigned to (an access grant on the portfolio, program or
# project, or a resource allocation to the project), or every project in the account.
SCOPE_ASSIGNED = "ASSIGNED"
SCOPE_ALL = "ALL"


class AccountRole(Base):
    """A role people in an account hold: rights per area, and how far they reach.

    Built-in roles carry a `key` (OWNER, PORTFOLIO_MANAGER, ...); owners can change every built-in role except
    Owner and add their own. `permissions` is {area: [action, ...]}.
    """

    __tablename__ = "account_roles"
    __table_args__ = (
        UniqueConstraint("account_id", "name", name="uq_account_roles_account_id_name"),
        UniqueConstraint("account_id", "key", name="uq_account_roles_account_id_key"),
        CheckConstraint("scope in ('ASSIGNED', 'ALL')", name="ck_account_roles_scope"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions: Mapped[dict[str, list[str]]] = mapped_column(JSON, nullable=False, default=dict)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default=SCOPE_ASSIGNED, server_default=SCOPE_ASSIGNED)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
