"""link tasks to RAID, decision and change request records

The project sheet's Related Record column: a list of {"type", "id"} pairs on each task.

Revision ID: 202611030200
Revises: 202611030100
Create Date: 2026-11-03 02:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202611030200"
down_revision: Union[str, Sequence[str], None] = "202611030100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("related_records", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "related_records")
