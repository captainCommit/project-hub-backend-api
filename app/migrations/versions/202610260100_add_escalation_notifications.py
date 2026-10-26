"""add escalation notifications

Escalating a risk, issue, assumption or dependency notifies the chosen person or team and emails them straight away.
The email shows a snapshot of the item and who it was escalated to, kept in the notification's new details column.

Revision ID: 202610260100
Revises: 202610230100
Create Date: 2026-10-26 01:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610260100"
down_revision: Union[str, Sequence[str], None] = "202610230100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TYPES = (
    "'TASK_ASSIGNED', 'COMMENT_ADDED', 'MENTION', 'STATUS_CHANGED', 'RISK_CREATED', 'DECISION_APPROVED', 'DUE_SOON', "
    "'SUPPORT_REPLY'"
)


def upgrade() -> None:
    op.add_column("notifications", sa.Column("details", sa.JSON(), nullable=True))
    op.drop_constraint("ck_notifications_notification_type", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_notification_type", "notifications", f"notification_type in ({OLD_TYPES}, 'ESCALATION')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM notifications WHERE notification_type = 'ESCALATION'")
    op.drop_constraint("ck_notifications_notification_type", "notifications", type_="check")
    op.create_check_constraint("ck_notifications_notification_type", "notifications", f"notification_type in ({OLD_TYPES})")
    op.drop_column("notifications", "details")
