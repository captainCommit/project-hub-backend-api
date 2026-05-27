"""add global search

Revision ID: 202605260400
Revises: 202605260300
Create Date: 2026-05-26 10:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202605260400"
down_revision: Union[str, Sequence[str], None] = "202605260300"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TRIGRAM_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_portfolios_name_trgm", "portfolios", "name"),
    ("ix_programs_name_trgm", "programs", "name"),
    ("ix_projects_name_trgm", "projects", "name"),
    ("ix_tasks_name_trgm", "tasks", "name"),
    ("ix_risks_title_trgm", "risks", "title"),
    ("ix_issues_title_trgm", "issues", "title"),
    ("ix_assumptions_description_trgm", "assumptions", "description"),
    ("ix_decisions_decision_text_trgm", "decisions", "decision_text"),
)


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("decisions", sa.Column("decision_text", sa.Text(), nullable=True))
    op.execute("UPDATE decisions SET decision_text = title WHERE decision_text IS NULL")

    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        for index_name, table_name, column_name in TRIGRAM_INDEXES:
            op.create_index(
                index_name,
                table_name,
                [column_name],
                postgresql_using="gin",
                postgresql_ops={column_name: "gin_trgm_ops"},
            )
        return

    for index_name, table_name, column_name in TRIGRAM_INDEXES:
        op.create_index(index_name, table_name, [column_name])


def downgrade() -> None:
    for index_name, table_name, _column_name in reversed(TRIGRAM_INDEXES):
        op.drop_index(index_name, table_name=table_name)
    op.drop_column("decisions", "decision_text")