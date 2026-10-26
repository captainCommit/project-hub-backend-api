"""turn the AI assistant on per account

A platform admin switches AI on for an account; until then the account has no Ask Compass, reports or AI settings,
and owners can request it from Account > Connected apps. Accounts already using AI (their own key, or the platform
model switched on) keep it.

Revision ID: 202610260300
Revises: 202610260200
Create Date: 2026-10-26 03:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610260300"
down_revision: Union[str, Sequence[str], None] = "202610260200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("ai_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("accounts", sa.Column("ai_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("accounts", sa.Column("ai_requested_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_accounts_ai_requested_by_users", "accounts", "users", ["ai_requested_by"], ["id"], ondelete="SET NULL"
    )
    op.execute(
        """
        UPDATE accounts SET ai_enabled = true
        WHERE id IN (SELECT account_id FROM ai_account_settings WHERE encrypted_key IS NOT NULL)
           OR id IN (SELECT account_id FROM ai_platform_access WHERE status = 'ENABLED')
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_accounts_ai_requested_by_users", "accounts", type_="foreignkey")
    op.drop_column("accounts", "ai_requested_by")
    op.drop_column("accounts", "ai_requested_at")
    op.drop_column("accounts", "ai_enabled")
