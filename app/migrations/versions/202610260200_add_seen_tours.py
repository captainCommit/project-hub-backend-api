"""remember which first-time tours each person has seen

Each module shows a short tour the first time someone opens it. The tours are defined in the UI; this keeps the
ids of the ones a person finished or skipped, so they don't come back on another browser.

Revision ID: 202610260200
Revises: 202610260100
Create Date: 2026-10-26 02:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610260200"
down_revision: Union[str, Sequence[str], None] = "202610260100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("seen_tours", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "seen_tours")
