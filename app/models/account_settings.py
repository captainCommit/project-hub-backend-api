from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, JSON, String, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


DEFAULT_DATE_FORMAT = "MM/dd/yyyy"
DEFAULT_LANDING_PAGE = "PORTFOLIOS"
DEFAULT_NON_WORKING_WEEKDAYS = ["SATURDAY", "SUNDAY"]


def default_non_working_weekdays() -> list[str]:
    return DEFAULT_NON_WORKING_WEEKDAYS.copy()


class AccountSettings(Base):
    __tablename__ = "account_settings"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_account_settings_account_id"),
        CheckConstraint(
            "date_format in ('MM/dd/yyyy', 'dd/MM/yyyy', 'yyyy-MM-dd')",
            name="ck_account_settings_date_format",
        ),
        CheckConstraint(
            "default_landing_page in ('FAVORITES', 'PORTFOLIOS', 'PROGRAMS', 'SPRINTS')",
            name="ck_account_settings_default_landing_page",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    date_format: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DEFAULT_DATE_FORMAT,
        server_default=DEFAULT_DATE_FORMAT,
    )
    default_landing_page: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=DEFAULT_LANDING_PAGE,
        server_default=DEFAULT_LANDING_PAGE,
    )
    hide_delivery_section: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    non_working_weekdays: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=default_non_working_weekdays,
        server_default=text("'[\"SATURDAY\", \"SUNDAY\"]'"),
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