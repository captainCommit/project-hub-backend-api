"""add assumption/dependency target dates and the home bell read time

Assumptions gain an assignee and a target date, dependencies gain a target date, and each person
remembers when they last cleared the Home page bell.

Revision ID: 202610150400
Revises: 202610150300
Create Date: 2026-10-15 04:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610150400"
down_revision: Union[str, Sequence[str], None] = "202610150300"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assumptions", sa.Column("assigned_to_name", sa.String(length=255), nullable=True))
    op.add_column("assumptions", sa.Column("target_date", sa.Date(), nullable=True))
    op.add_column("dependencies", sa.Column("target_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("home_notifications_read_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "home_notifications_read_at")
    op.drop_column("dependencies", "target_date")
    op.drop_column("assumptions", "target_date")
    op.drop_column("assumptions", "assigned_to_name")
