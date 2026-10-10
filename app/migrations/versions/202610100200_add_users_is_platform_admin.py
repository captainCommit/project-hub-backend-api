"""add users.is_platform_admin

Platform admins create accounts and can manage users in any account.
Grant it with scripts/grant_platform_admin.py.

Revision ID: 202610100200
Revises: 202610100100
Create Date: 2026-10-10 02:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610100200"
down_revision: Union[str, Sequence[str], None] = "202610100100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("users", "is_platform_admin")
