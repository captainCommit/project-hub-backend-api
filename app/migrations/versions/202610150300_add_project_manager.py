"""add project manager

The program page lets you set each project's manager inline, picking from the account's resources.

Revision ID: 202610150300
Revises: 202610150200
Create Date: 2026-10-15 03:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610150300"
down_revision: Union[str, Sequence[str], None] = "202610150200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("project_manager", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "project_manager")
