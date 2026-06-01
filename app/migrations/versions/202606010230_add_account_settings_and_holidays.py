"""add account settings and holidays

Revision ID: 202606010230
Revises: 202605281000
Create Date: 2026-06-01 02:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202606010230"
down_revision: Union[str, Sequence[str], None] = "202605281000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_settings",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("date_format", sa.String(length=20), server_default="MM/dd/yyyy", nullable=False),
        sa.Column("default_landing_page", sa.String(length=50), server_default="PORTFOLIOS", nullable=False),
        sa.Column("hide_delivery_section", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "non_working_weekdays",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[\"SATURDAY\", \"SUNDAY\"]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "date_format in ('MM/dd/yyyy', 'dd/MM/yyyy', 'yyyy-MM-dd')",
            name="ck_account_settings_date_format",
        ),
        sa.CheckConstraint(
            "default_landing_page in ('FAVORITES', 'PORTFOLIOS', 'PROGRAMS', 'SPRINTS')",
            name="ck_account_settings_default_landing_page",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", name="uq_account_settings_account_id"),
    )
    op.create_table(
        "account_holidays",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_holidays_account_id_holiday_date",
        "account_holidays",
        ["account_id", "holiday_date"],
    )
    op.create_index(
        "ix_account_holidays_account_id_is_active",
        "account_holidays",
        ["account_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_account_holidays_account_id_is_active", table_name="account_holidays")
    op.drop_index("ix_account_holidays_account_id_holiday_date", table_name="account_holidays")
    op.drop_table("account_holidays")
    op.drop_table("account_settings")