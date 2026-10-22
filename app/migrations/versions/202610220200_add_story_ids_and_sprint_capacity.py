"""add story ids and sprint capacity

Each task gets a story id within its project (shown as US-100, US-101, ...); existing tasks are numbered in the order
they were created. Sprints get a capacity in story points.

Revision ID: 202610220200
Revises: 202610220100
Create Date: 2026-10-22 02:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610220200"
down_revision: Union[str, Sequence[str], None] = "202610220100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("story_number", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE tasks
        SET story_number = numbered.story_number
        FROM (
            SELECT id, 99 + ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY created_at, id) AS story_number
            FROM tasks
        ) AS numbered
        WHERE tasks.id = numbered.id
        """
    )
    op.create_unique_constraint("uq_tasks_project_id_story_number", "tasks", ["project_id", "story_number"])
    op.add_column("sprints", sa.Column("capacity", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("sprints", "capacity")
    op.drop_constraint("uq_tasks_project_id_story_number", "tasks", type_="unique")
    op.drop_column("tasks", "story_number")
