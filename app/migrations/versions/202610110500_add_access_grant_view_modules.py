"""add access_grants.view_modules

The modules a member or viewer may see. NULL keeps today's behaviour: every module is visible.

Revision ID: 202610110500
Revises: 202610110400
Create Date: 2026-10-11 05:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610110500"
down_revision: Union[str, Sequence[str], None] = "202610110400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("access_grants", sa.Column("view_modules", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("access_grants", "view_modules")
