"""status & priority labels: renamed work-status and health labels per account

Revision ID: 202611210200
Revises: 202611210100
Create Date: 2026-11-21 02:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "202611210200"
down_revision: Union[str, Sequence[str], None] = "202611210100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "account_settings",
        sa.Column("status_labels", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("account_settings", "status_labels")
