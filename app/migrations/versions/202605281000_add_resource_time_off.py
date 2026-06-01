"""add resource time off

Revision ID: 202605281000
Revises: 202605271450
Create Date: 2026-05-28 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202605281000"
down_revision: Union[str, Sequence[str], None] = "202605271450"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resource_time_off",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resource_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("hours_per_day", sa.Numeric(10, 2), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="ck_resource_time_off_date_range"),
        sa.CheckConstraint(
            "hours_per_day is null or hours_per_day > 0",
            name="ck_resource_time_off_hours_per_day_positive",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_resource_time_off_account_id_resource_id",
        "resource_time_off",
        ["account_id", "resource_id"],
    )
    op.create_index(
        "ix_resource_time_off_start_date_end_date",
        "resource_time_off",
        ["start_date", "end_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_resource_time_off_start_date_end_date", table_name="resource_time_off")
    op.drop_index("ix_resource_time_off_account_id_resource_id", table_name="resource_time_off")
    op.drop_table("resource_time_off")