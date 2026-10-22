"""add portfolio and program owners

The portfolio and program directories show who owns each one, as projects show their manager.

Revision ID: 202610220100
Revises: 202610150600
Create Date: 2026-10-22 01:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610220100"
down_revision: Union[str, Sequence[str], None] = "202610150600"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("portfolios", sa.Column("owner", sa.String(length=255), nullable=True))
    op.add_column("programs", sa.Column("owner", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("programs", "owner")
    op.drop_column("portfolios", "owner")
