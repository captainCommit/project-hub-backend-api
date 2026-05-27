"""add resources and resource allocations

Revision ID: 202605271450
Revises: 202605270930
Create Date: 2026-05-27 14:50:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202605271450"
down_revision: Union[str, Sequence[str], None] = "202605270930"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resources",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=255), nullable=True),
        sa.Column("weekly_capacity_hours", sa.Numeric(10, 2), server_default="40", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("weekly_capacity_hours > 0", name="ck_resources_weekly_capacity_hours_positive"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resources_account_id", "resources", ["account_id"])
    op.create_index("ix_resources_account_id_user_id", "resources", ["account_id", "user_id"])

    op.create_table(
        "resource_allocations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resource_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("allocated_hours", sa.Numeric(10, 2), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "allocated_hours is null or allocated_hours > 0",
            name="ck_resource_allocations_allocated_hours_positive",
        ),
        sa.CheckConstraint(
            "start_date is null or end_date is null or end_date >= start_date",
            name="ck_resource_allocations_date_range",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_resource_allocations_account_id_resource_id",
        "resource_allocations",
        ["account_id", "resource_id"],
    )
    op.create_index(
        "ix_resource_allocations_account_id_task_id",
        "resource_allocations",
        ["account_id", "task_id"],
    )
    op.create_index(
        "ix_resource_allocations_start_date_end_date",
        "resource_allocations",
        ["start_date", "end_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_resource_allocations_start_date_end_date", table_name="resource_allocations")
    op.drop_index("ix_resource_allocations_account_id_task_id", table_name="resource_allocations")
    op.drop_index("ix_resource_allocations_account_id_resource_id", table_name="resource_allocations")
    op.drop_table("resource_allocations")
    op.drop_index("ix_resources_account_id_user_id", table_name="resources")
    op.drop_index("ix_resources_account_id", table_name="resources")
    op.drop_table("resources")