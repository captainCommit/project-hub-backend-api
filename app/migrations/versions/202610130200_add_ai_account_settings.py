"""add ai account settings

Owners' assistant settings: on/off, chat retention, and the account's own (encrypted) model key.

Revision ID: 202610130200
Revises: 202610130100
Create Date: 2026-10-13 02:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610130200"
down_revision: Union[str, Sequence[str], None] = "202610130100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROVIDERS = "'OPENAI', 'ANTHROPIC', 'GEMINI', 'AZURE_OPENAI', 'CUSTOM'"


def upgrade() -> None:
    op.create_table(
        "ai_account_settings",
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assistant_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("retention_days", sa.Integer(), server_default=sa.text("90"), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("azure_api_version", sa.String(length=40), nullable=True),
        sa.Column("encrypted_key", sa.LargeBinary(), nullable=True),
        sa.Column("key_last4", sa.String(length=8), nullable=True),
        sa.Column("key_updated_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("key_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_notice_accepted_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("data_notice_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"provider is null or provider in ({PROVIDERS})", name="ck_ai_account_settings_provider"),
        sa.CheckConstraint(
            "retention_days >= 1 and retention_days <= 3650", name="ck_ai_account_settings_retention_days"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["key_updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["data_notice_accepted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("account_id"),
    )


def downgrade() -> None:
    op.drop_table("ai_account_settings")
