"""add task soft delete columns

Revision ID: 202605270930
Revises: 202605260600
Create Date: 2026-05-27 09:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202605270930"
down_revision: Union[str, Sequence[str], None] = "202605260600"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("tasks", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("deleted_by", sa.Uuid(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_tasks_deleted_by_users",
        "tasks",
        "users",
        ["deleted_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_tasks_is_deleted", "tasks", ["is_deleted"])
    op.execute(sa.text("UPDATE tasks SET is_deleted = false WHERE is_deleted IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_tasks_is_deleted", table_name="tasks")
    op.drop_constraint("fk_tasks_deleted_by_users", "tasks", type_="foreignkey")
    op.drop_column("tasks", "deleted_by")
    op.drop_column("tasks", "deleted_at")
    op.drop_column("tasks", "is_deleted")