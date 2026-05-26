"""add configurable options

Revision ID: 202605260050
Revises: 202605260024
Create Date: 2026-05-26 00:50:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202605260050"
down_revision: Union[str, Sequence[str], None] = "202605260024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "option_sets",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "entity_type", "name", name="uq_option_sets_account_id_entity_type_name"),
    )

    op.create_table(
        "option_values",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("option_set_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("value", sa.String(length=100), nullable=False),
        sa.Column("color", sa.String(length=50), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["option_set_id"], ["option_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("option_set_id", "value", name="uq_option_values_option_set_id_value"),
    )


def downgrade() -> None:
    op.drop_table("option_values")
    op.drop_table("option_sets")