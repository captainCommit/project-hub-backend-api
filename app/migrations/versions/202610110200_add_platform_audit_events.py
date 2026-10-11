"""add platform_audit_events

Append-only record of what platform admins do to customer accounts.

Revision ID: 202610110200
Revises: 202610110100
Create Date: 2026-10-11 02:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610110200"
down_revision: Union[str, Sequence[str], None] = "202610110100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("target", sa.String(length=255), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_audit_events_created_at", "platform_audit_events", ["created_at"])
    op.create_index(
        "ix_platform_audit_events_account_id_created_at",
        "platform_audit_events",
        ["account_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_platform_audit_events_account_id_created_at", table_name="platform_audit_events")
    op.drop_index("ix_platform_audit_events_created_at", table_name="platform_audit_events")
    op.drop_table("platform_audit_events")
