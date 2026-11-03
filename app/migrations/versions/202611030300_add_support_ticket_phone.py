"""let support ticket requesters say they're open to a phone call

Revision ID: 202611030300
Revises: 202611030200
Create Date: 2026-11-03 03:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202611030300"
down_revision: Union[str, Sequence[str], None] = "202611030200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("support_tickets", sa.Column("phone_call_ok", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("support_tickets", sa.Column("phone_number", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("support_tickets", "phone_number")
    op.drop_column("support_tickets", "phone_call_ok")
