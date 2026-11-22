"""recently deleted: deleted portfolios, programs and projects kept for restore

Revision ID: 202611210100
Revises: 202611100100
Create Date: 2026-11-21 01:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202611210100"
down_revision: Union[str, Sequence[str], None] = "202611100100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deleted_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=600), nullable=True),
        sa.Column("project_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deleted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deleted_records_account_id_deleted_at", "deleted_records", ["account_id", "deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_deleted_records_account_id_deleted_at", table_name="deleted_records")
    op.drop_table("deleted_records")
