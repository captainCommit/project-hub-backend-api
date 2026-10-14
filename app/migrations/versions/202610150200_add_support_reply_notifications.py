"""add support reply notifications

Project Hub support can answer customers' support tickets from the admin portal; the requester is notified.

Revision ID: 202610150200
Revises: 202610150100
Create Date: 2026-10-15 02:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "202610150200"
down_revision: Union[str, Sequence[str], None] = "202610150100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TYPES = "'TASK_ASSIGNED', 'COMMENT_ADDED', 'MENTION', 'STATUS_CHANGED', 'RISK_CREATED', 'DECISION_APPROVED', 'DUE_SOON'"


def upgrade() -> None:
    op.drop_constraint("ck_notifications_notification_type", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_notification_type", "notifications", f"notification_type in ({OLD_TYPES}, 'SUPPORT_REPLY')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM notifications WHERE notification_type = 'SUPPORT_REPLY'")
    op.drop_constraint("ck_notifications_notification_type", "notifications", type_="check")
    op.create_check_constraint("ck_notifications_notification_type", "notifications", f"notification_type in ({OLD_TYPES})")
