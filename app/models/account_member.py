from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AccountMemberRole(str, Enum):
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"


class AccountMember(Base):
    __tablename__ = "account_members"
    __table_args__ = (
        UniqueConstraint("account_id", "user_id", name="uq_account_members_account_id_user_id"),
        CheckConstraint(
            "role in ('OWNER', 'MANAGER', 'MEMBER', 'VIEWER')",
            name="ck_account_members_role",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
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
    # The seat: OWNER, or MEMBER (a full seat) / VIEWER (a viewer seat), kept in step with role_id's rights.
    # MANAGER is no longer given out; access comes from the account role.
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    # What they can do (Account > Users & Roles).
    role_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("account_roles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )