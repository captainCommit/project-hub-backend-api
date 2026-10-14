"""remove teams

Microsoft Teams was dropped from the integrations: remove its conversations table, the user-link column that
pointed at a Teams personal chat, and any Teams connections (with their channels and links, by cascade).

Revision ID: 202610150100
Revises: 202610140100
Create Date: 2026-10-15 01:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610150100"
down_revision: Union[str, Sequence[str], None] = "202610140100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM integration_connections WHERE provider = 'TEAMS'")
    op.drop_constraint("ck_integration_connections_provider", "integration_connections", type_="check")
    op.create_check_constraint(
        "ck_integration_connections_provider", "integration_connections", "provider in ('SLACK', 'MICROSOFT_MAIL')"
    )
    op.drop_constraint("integration_user_links_conversation_id_fkey", "integration_user_links", type_="foreignkey")
    op.drop_column("integration_user_links", "conversation_id")
    op.drop_index("ix_integration_conversations_tenant", table_name="integration_conversations")
    op.drop_table("integration_conversations")


def downgrade() -> None:
    op.create_table(
        "integration_conversations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("tenant_id", sa.String(length=100), nullable=False),
        sa.Column("external_id", sa.String(length=300), nullable=False),
        sa.Column("service_url", sa.String(length=500), nullable=False),
        sa.Column("team_id", sa.String(length=300), nullable=True),
        sa.Column("team_name", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("user_external_id", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind in ('CHANNEL', 'PERSONAL')", name="ck_integration_conversations_kind"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "external_id", name="uq_integration_conversations_provider_external"),
    )
    op.create_index("ix_integration_conversations_tenant", "integration_conversations", ["provider", "tenant_id"])
    op.add_column("integration_user_links", sa.Column("conversation_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "integration_user_links_conversation_id_fkey",
        "integration_user_links",
        "integration_conversations",
        ["conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("ck_integration_connections_provider", "integration_connections", type_="check")
    op.create_check_constraint(
        "ck_integration_connections_provider", "integration_connections", "provider in ('SLACK', 'TEAMS', 'MICROSOFT_MAIL')"
    )
