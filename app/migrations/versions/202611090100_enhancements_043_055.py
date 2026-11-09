"""enhancements 043-055: pinned projects, D- dependency numbers, quantified decision effort,
change implementation fields, weekly allocation overrides and a story's requirement

Revision ID: 202611090100
Revises: 202611030300
Create Date: 2026-11-09 01:00:00.000000
"""

import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202611090100"
down_revision: Union[str, Sequence[str], None] = "202611030300"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EFFORT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|d|day|days|w|wk|wks|week|weeks)\s*$", re.IGNORECASE)
EFFORT_UNITS = {"h": "HOURS", "d": "DAYS", "w": "WEEKS"}


def backfill_work_effort() -> None:
    """Options that already say something like "2 weeks" become a value and a unit; anything else stays as text."""
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, work_effort FROM decision_options WHERE work_effort IS NOT NULL")).fetchall()
    update = sa.text("UPDATE decision_options SET work_effort_value = :value, work_effort_unit = :unit WHERE id = :id")
    for option_id, text in rows:
        match = EFFORT.match(text)
        if match:
            bind.execute(update, {"id": option_id, "value": match.group(1), "unit": EFFORT_UNITS[match.group(2)[0].lower()]})


def upgrade() -> None:
    op.create_table(
        "pinned_projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "project_id", name="uq_pinned_projects_user_id_project_id"),
    )
    op.create_table(
        "resource_project_allocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("weekly_hours", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("weekly_hours >= 0", name="ck_resource_project_allocations_weekly_hours_non_negative"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_id", "project_id", name="uq_resource_project_allocations_resource_id_project_id"),
    )
    op.create_index("ix_resource_project_allocations_account_id", "resource_project_allocations", ["account_id"])

    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("requirement_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_tasks_requirement_id_requirements", "requirements", ["requirement_id"], ["id"], ondelete="SET NULL"
        )

    op.add_column("decision_options", sa.Column("work_effort_value", sa.Numeric(10, 2), nullable=True))
    op.add_column("decision_options", sa.Column("work_effort_unit", sa.String(length=10), nullable=True))
    op.add_column("decision_options", sa.Column("implementation_resources", sa.JSON(), nullable=True))
    backfill_work_effort()

    op.add_column("change_requests", sa.Column("implementation_resources", sa.JSON(), nullable=True))
    op.add_column("change_requests", sa.Column("completion_date", sa.Date(), nullable=True))

    # DEP-003 becomes D-003. A project that somehow already has that D- number keeps its old one rather than failing.
    op.execute(
        """
        UPDATE dependencies
        SET dependency_number = 'D-' || substr(dependency_number, 5)
        WHERE dependency_number LIKE 'DEP-%'
          AND NOT EXISTS (
            SELECT 1 FROM dependencies AS taken
            WHERE taken.project_id = dependencies.project_id
              AND taken.dependency_number = 'D-' || substr(dependencies.dependency_number, 5)
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE dependencies
        SET dependency_number = 'DEP-' || substr(dependency_number, 3)
        WHERE dependency_number LIKE 'D-%'
          AND NOT EXISTS (
            SELECT 1 FROM dependencies AS taken
            WHERE taken.project_id = dependencies.project_id
              AND taken.dependency_number = 'DEP-' || substr(dependencies.dependency_number, 3)
          )
        """
    )
    op.drop_column("change_requests", "completion_date")
    op.drop_column("change_requests", "implementation_resources")
    op.drop_column("decision_options", "implementation_resources")
    op.drop_column("decision_options", "work_effort_unit")
    op.drop_column("decision_options", "work_effort_value")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_requirement_id_requirements", type_="foreignkey")
        batch.drop_column("requirement_id")
    op.drop_index("ix_resource_project_allocations_account_id", table_name="resource_project_allocations")
    op.drop_table("resource_project_allocations")
    op.drop_table("pinned_projects")
