"""compass: what happened to a drafted risk, issue or task, and thumbs up/down on answers

Revision ID: 202611210600
Revises: 202611210500
Create Date: 2026-11-21 06:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202611210600"
down_revision: Union[str, Sequence[str], None] = "202611210500"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_messages", sa.Column("outcome", sa.JSON(), nullable=True))
    op.add_column("ai_messages", sa.Column("feedback", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_messages", "feedback")
    op.drop_column("ai_messages", "outcome")
