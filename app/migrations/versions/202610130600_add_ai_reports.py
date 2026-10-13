"""add ai reports

Written reports (status reports, health summaries, what changed) on a project, program or portfolio.

Revision ID: 202610130600
Revises: 202610130500
Create Date: 2026-10-13 06:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610130600"
down_revision: Union[str, Sequence[str], None] = "202610130500"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_reports",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scope_name", sa.String(length=255), nullable=False),
        sa.Column("report_type", sa.String(length=40), nullable=False),
        sa.Column("since", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("scope_type in ('PROJECT', 'PROGRAM', 'PORTFOLIO')", name="ck_ai_reports_scope_type"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["ai_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_reports_user_scope_created", "ai_reports", ["user_id", "scope_type", "scope_id", "created_at"]
    )
    op.create_index("ix_ai_reports_account_created", "ai_reports", ["account_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_reports_account_created", table_name="ai_reports")
    op.drop_index("ix_ai_reports_user_scope_created", table_name="ai_reports")
    op.drop_table("ai_reports")
