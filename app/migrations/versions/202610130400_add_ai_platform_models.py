"""add ai platform models

Models the platform offers to accounts without their own key, each account's request and terms (allowance,
cap), each run's charge, and the subscription item that platform AI usage is billed to.

Revision ID: 202610130400
Revises: 202610130300
Create Date: 2026-10-13 04:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610130400"
down_revision: Union[str, Sequence[str], None] = "202610130300"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_platform_models",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("input_cost_per_mtok_cents", sa.Numeric(12, 4), nullable=False),
        sa.Column("output_cost_per_mtok_cents", sa.Numeric(12, 4), nullable=False),
        sa.Column("margin_percent", sa.Numeric(6, 2), server_default="30", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("provider in ('OPENAI', 'ANTHROPIC', 'GEMINI')", name="ck_ai_platform_models_provider"),
        sa.CheckConstraint(
            "input_cost_per_mtok_cents >= 0 and output_cost_per_mtok_cents >= 0 and margin_percent >= 0",
            name="ck_ai_platform_models_prices",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "ai_platform_access",
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("platform_model_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("allowance_kind", sa.String(length=10), nullable=False),
        sa.Column("allowance_amount", sa.BigInteger(), nullable=False),
        sa.Column("monthly_cap_cents", sa.Integer(), nullable=True),
        sa.Column("requested_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status in ('REQUESTED', 'ENABLED', 'DISABLED', 'DECLINED')", name="ck_ai_platform_access_status"
        ),
        sa.CheckConstraint("allowance_kind in ('TOKENS', 'CREDIT')", name="ck_ai_platform_access_allowance_kind"),
        sa.CheckConstraint("allowance_amount >= 0", name="ck_ai_platform_access_allowance_amount"),
        sa.CheckConstraint("monthly_cap_cents is null or monthly_cap_cents >= 0", name="ck_ai_platform_access_cap"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["platform_model_id"], ["ai_platform_models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("account_id"),
    )

    op.add_column("ai_runs", sa.Column("platform_model_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_ai_runs_platform_model_id", "ai_runs", "ai_platform_models", ["platform_model_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column("ai_runs", sa.Column("charge_cents", sa.Numeric(14, 4), server_default="0", nullable=False))
    op.add_column("account_billing", sa.Column("ai_usage_item_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("account_billing", "ai_usage_item_id")
    op.drop_column("ai_runs", "charge_cents")
    op.drop_constraint("fk_ai_runs_platform_model_id", "ai_runs", type_="foreignkey")
    op.drop_column("ai_runs", "platform_model_id")
    op.drop_table("ai_platform_access")
    op.drop_table("ai_platform_models")
