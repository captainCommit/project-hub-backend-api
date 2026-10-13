"""add ai conversations

People's conversations with the assistant, their messages, and one run per question (progress and tokens).

Revision ID: 202610130300
Revises: 202610130200
Create Date: 2026-10-13 03:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610130300"
down_revision: Union[str, Sequence[str], None] = "202610130200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_conversations_account_user_updated", "ai_conversations", ["account_id", "user_id", "updated_at"]
    )

    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress", sa.String(length=200), nullable=True),
        sa.Column("error_kind", sa.String(length=30), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("model_calls", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status in ('QUEUED', 'RUNNING', 'DONE', 'FAILED')", name="ck_ai_runs_status"),
        sa.CheckConstraint("source is null or source in ('BYOK', 'PLATFORM')", name="ck_ai_runs_source"),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_runs_account_created", "ai_runs", ["account_id", "created_at"])
    op.create_index("ix_ai_runs_user_created", "ai_runs", ["user_id", "created_at"])
    op.create_index("ix_ai_runs_conversation", "ai_runs", ["conversation_id"])

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role in ('user', 'assistant', 'tool')", name="ck_ai_messages_role"),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["ai_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "position", name="uq_ai_messages_conversation_position"),
    )


def downgrade() -> None:
    op.drop_table("ai_messages")
    op.drop_index("ix_ai_runs_conversation", table_name="ai_runs")
    op.drop_index("ix_ai_runs_user_created", table_name="ai_runs")
    op.drop_index("ix_ai_runs_account_created", table_name="ai_runs")
    op.drop_table("ai_runs")
    op.drop_index("ix_ai_conversations_account_user_updated", table_name="ai_conversations")
    op.drop_table("ai_conversations")
