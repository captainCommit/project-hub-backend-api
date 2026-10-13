"""add my work summary

The daily email's My work summary (IN-2) can be turned off, and we record whether the timezone came from the
person (or their device) rather than the default, so the app can fill it in for everyone else.
Due-soon reminders default to the daily digest instead of right away.

Revision ID: 202610130100
Revises: 202610120100
Create Date: 2026-10-13 01:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610130100"
down_revision: Union[str, Sequence[str], None] = "202610120100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Due-soon reminders now join the daily digest by default, next to the summary that lists the same tasks.
    # Move everyone still on the old defaults (rows the morning run created, not settings anyone saved).
    op.alter_column("notification_preferences", "due_soon", server_default="DIGEST")
    op.execute(
        "update notification_preferences set due_soon = 'DIGEST' "
        "where due_soon = 'IMMEDIATE' and mentions = 'IMMEDIATE' and assignments = 'IMMEDIATE' "
        "and status_changes = 'DIGEST' and comments = 'OFF' "
        "and timezone = 'America/Toronto' and digest_hour = 8"
    )
    op.add_column(
        "notification_preferences",
        sa.Column("my_work_summary", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("timezone_confirmed", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    # Anyone who already picked a zone other than the default chose it themselves; don't overwrite it.
    op.execute("update notification_preferences set timezone_confirmed = true where timezone <> 'America/Toronto'")


def downgrade() -> None:
    op.drop_column("notification_preferences", "timezone_confirmed")
    op.drop_column("notification_preferences", "my_work_summary")
    op.alter_column("notification_preferences", "due_soon", server_default="IMMEDIATE")
