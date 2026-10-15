"""index the foreign keys the app looks things up by

Postgres doesn't index foreign keys on its own. Columns already covered by an (account_id, x) index are
left alone; these are the lookups with no usable index at all, which read the whole table today.

Revision ID: 202610150600
Revises: 202610150500
Create Date: 2026-10-15 06:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "202610150600"
down_revision: Union[str, Sequence[str], None] = "202610150500"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEXES: list[tuple[str, str, list[str]]] = [
    # Who a task is assigned to (Home, My work, digests), and a task's assignees.
    ("ix_task_assignments_user_id_task_id", "task_assignments", ["user_id", "task_id"]),
    ("ix_task_assignments_task_id", "task_assignments", ["task_id"]),
    # A task's successors.
    ("ix_task_predecessors_predecessor_task_id", "task_predecessors", ["predecessor_task_id"]),
    # Which accounts a user belongs to.
    ("ix_account_members_user_id", "account_members", ["user_id"]),
    # A person's mentions (Home, digests).
    ("ix_comment_mentions_mentioned_user_id", "comment_mentions", ["mentioned_user_id"]),
    ("ix_comments_parent_comment_id", "comments", ["parent_comment_id"]),
    # Recent activity, newest first.
    ("ix_activity_log_account_id_created_at", "activity_log", ["account_id", "created_at"]),
    ("ix_decision_options_decision_id", "decision_options", ["decision_id"]),
    ("ix_test_cases_requirement_id", "test_cases", ["requirement_id"]),
    ("ix_ai_messages_run_id", "ai_messages", ["run_id"]),
    ("ix_integration_destinations_connection_id", "integration_destinations", ["connection_id"]),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
