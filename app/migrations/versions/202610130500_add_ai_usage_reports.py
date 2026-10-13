"""add ai usage reports

How many cents of each account's monthly platform AI usage have been reported to Stripe.

Revision ID: 202610130500
Revises: 202610130400
Create Date: 2026-10-13 05:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610130500"
down_revision: Union[str, Sequence[str], None] = "202610130400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_reports",
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("month_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_cents", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "month_start"),
    )


def downgrade() -> None:
    op.drop_table("ai_usage_reports")
