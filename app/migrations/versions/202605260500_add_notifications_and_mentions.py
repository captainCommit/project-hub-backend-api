"""add notifications and comment mentions

Revision ID: 202605260500
Revises: 202605260400
Create Date: 2026-05-26 12:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202605260500"
down_revision: Union[str, Sequence[str], None] = "202605260400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("notification_type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "notification_type in ("
            "'TASK_ASSIGNED', 'COMMENT_ADDED', 'MENTION', "
            "'STATUS_CHANGED', 'RISK_CREATED', 'DECISION_APPROVED'"
            ")",
            name="ck_notifications_notification_type",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_id_is_read_created_at", "notifications", ["user_id", "is_read", "created_at"])

    op.create_table(
        "comment_mentions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("comment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("mentioned_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mentioned_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("comment_id", "mentioned_user_id", name="uq_comment_mentions_comment_id_mentioned_user_id"),
    )
    op.create_index("ix_comment_mentions_comment_id", "comment_mentions", ["comment_id"])


def downgrade() -> None:
    op.drop_index("ix_comment_mentions_comment_id", table_name="comment_mentions")
    op.drop_table("comment_mentions")
    op.drop_index("ix_notifications_user_id_is_read_created_at", table_name="notifications")
    op.drop_table("notifications")