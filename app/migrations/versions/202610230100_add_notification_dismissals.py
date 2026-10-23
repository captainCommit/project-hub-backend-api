"""let people remove notifications from their bell

Stored notifications get a dismissed time, so they stay for email delivery but leave the bell. Home page bell
items are built from live data, so the ones a person removes are remembered by their item id.

Revision ID: 202610230100
Revises: 202610220200
Create Date: 2026-10-23 01:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610230100"
down_revision: Union[str, Sequence[str], None] = "202610220200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "home_notification_dismissals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("item_key", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "item_key", name="uq_home_notification_dismissals_user_id_item_key"),
    )


def downgrade() -> None:
    op.drop_table("home_notification_dismissals")
    op.drop_column("notifications", "dismissed_at")
