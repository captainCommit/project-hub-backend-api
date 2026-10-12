"""add email notifications

Email delivery state on notifications, a DUE_SOON notification type, per-person email preferences,
and the addresses SES reported as bounced or complained.

Revision ID: 202610120100
Revises: 202610110500
Create Date: 2026-10-12 01:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610120100"
down_revision: Union[str, Sequence[str], None] = "202610110500"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TYPES = "'TASK_ASSIGNED', 'COMMENT_ADDED', 'MENTION', 'STATUS_CHANGED', 'RISK_CREATED', 'DECISION_APPROVED'"
NEW_TYPES = f"{OLD_TYPES}, 'DUE_SOON'"
CATEGORIES = {
    "mentions": "IMMEDIATE",
    "assignments": "IMMEDIATE",
    "due_soon": "IMMEDIATE",
    "status_changes": "DIGEST",
    "comments": "OFF",
}


def upgrade() -> None:
    op.drop_constraint("ck_notifications_notification_type", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_notification_type", "notifications", f"notification_type in ({NEW_TYPES})"
    )
    # Existing notifications keep email_status NULL, so nothing old is emailed.
    op.add_column("notifications", sa.Column("email_status", sa.String(length=20), nullable=True))
    op.add_column(
        "notifications", sa.Column("email_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False)
    )
    op.add_column("notifications", sa.Column("email_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notifications", sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_notifications_email_status",
        "notifications",
        "email_status is null or email_status in ('PENDING', 'SENDING', 'DIGEST', 'SENT', 'SKIPPED', 'FAILED')",
    )
    op.create_index("ix_notifications_email_status_created_at", "notifications", ["email_status", "created_at"])

    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        *[
            sa.Column(category, sa.String(length=10), server_default=default, nullable=False)
            for category, default in CATEGORIES.items()
        ],
        sa.Column("timezone", sa.String(length=64), server_default="America/Toronto", nullable=False),
        sa.Column("digest_hour", sa.Integer(), server_default=sa.text("8"), nullable=False),
        sa.Column("last_digest_on", sa.Date(), nullable=True),
        sa.Column("last_due_soon_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        *[
            sa.CheckConstraint(
                f"{category} in ('IMMEDIATE', 'DIGEST', 'OFF')", name=f"ck_notification_preferences_{category}"
            )
            for category in CATEGORIES
        ],
        sa.CheckConstraint("digest_hour >= 0 and digest_hour <= 23", name="ck_notification_preferences_digest_hour"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "email_suppressions",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("reason in ('BOUNCE', 'COMPLAINT')", name="ck_email_suppressions_reason"),
        sa.PrimaryKeyConstraint("email"),
    )


def downgrade() -> None:
    op.drop_table("email_suppressions")
    op.drop_table("notification_preferences")
    op.drop_index("ix_notifications_email_status_created_at", table_name="notifications")
    op.drop_constraint("ck_notifications_email_status", "notifications", type_="check")
    op.drop_column("notifications", "emailed_at")
    op.drop_column("notifications", "email_claimed_at")
    op.drop_column("notifications", "email_attempts")
    op.drop_column("notifications", "email_status")
    op.execute("DELETE FROM notifications WHERE notification_type = 'DUE_SOON'")
    op.drop_constraint("ck_notifications_notification_type", "notifications", type_="check")
    op.create_check_constraint(
        "ck_notifications_notification_type", "notifications", f"notification_type in ({OLD_TYPES})"
    )
