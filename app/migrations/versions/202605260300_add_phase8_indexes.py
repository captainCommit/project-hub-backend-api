"""add phase 8 hardening indexes

Revision ID: 202605260300
Revises: 202605260200
Create Date: 2026-05-26 06:43:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "202605260300"
down_revision: Union[str, Sequence[str], None] = "202605260200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_account_members_account_id_user_id", "account_members", ["account_id", "user_id"]),
    ("ix_option_sets_account_id_entity_type_name", "option_sets", ["account_id", "entity_type", "name"]),
    ("ix_option_values_option_set_id_value", "option_values", ["option_set_id", "value"]),
    ("ix_portfolios_account_id", "portfolios", ["account_id"]),
    ("ix_programs_account_id_portfolio_id", "programs", ["account_id", "portfolio_id"]),
    ("ix_projects_account_id_program_id", "projects", ["account_id", "program_id"]),
    ("ix_tasks_account_id_project_id", "tasks", ["account_id", "project_id"]),
    ("ix_tasks_parent_task_id", "tasks", ["parent_task_id"]),
    ("ix_tasks_status_id", "tasks", ["status_id"]),
    ("ix_risks_account_id_project_id", "risks", ["account_id", "project_id"]),
    ("ix_issues_account_id_project_id", "issues", ["account_id", "project_id"]),
    ("ix_assumptions_account_id_project_id", "assumptions", ["account_id", "project_id"]),
    ("ix_decisions_account_id_project_id", "decisions", ["account_id", "project_id"]),
    ("ix_comments_account_id_entity_type_entity_id", "comments", ["account_id", "entity_type", "entity_id"]),
    ("ix_activity_log_account_id_entity_type_entity_id", "activity_log", ["account_id", "entity_type", "entity_id"]),
    ("ix_attachments_account_id_entity_type_entity_id", "attachments", ["account_id", "entity_type", "entity_id"]),
)


def upgrade() -> None:
    for index_name, table_name, columns in INDEXES:
        op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    for index_name, table_name, _columns in reversed(INDEXES):
        op.drop_index(index_name, table_name=table_name)