"""board view: an account can delete the Uncategorized column (it still shows while a task has no status)

Revision ID: 202611210500
Revises: 202611210400
Create Date: 2026-11-21 05:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202611210500"
down_revision: Union[str, Sequence[str], None] = "202611210400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "account_settings",
        sa.Column("hide_uncategorized_column", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("account_settings", "hide_uncategorized_column")
