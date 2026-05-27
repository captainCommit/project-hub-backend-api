"""add delivery type and sprints

Revision ID: 202605260600
Revises: 202605260500
Create Date: 2026-05-26 13:15:00.000000
"""

from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "202605260600"
down_revision: Union[str, Sequence[str], None] = "202605260500"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("delivery_type", sa.String(length=50), server_default="WATERFALL", nullable=False),
    )
    op.create_check_constraint(
        "ck_projects_delivery_type",
        "projects",
        "delivery_type in ('WATERFALL', 'AGILE', 'HYBRID')",
    )

    op.create_table(
        "sprints",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("status_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["status_id"], ["option_values.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sprints_account_id_project_id", "sprints", ["account_id", "project_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "ix_sprints_name_trgm",
            "sprints",
            ["name"],
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        )
        op.create_index(
            "ix_sprints_goal_trgm",
            "sprints",
            ["goal"],
            postgresql_using="gin",
            postgresql_ops={"goal": "gin_trgm_ops"},
        )

    op.add_column("tasks", sa.Column("sprint_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_tasks_sprint_id_sprints", "tasks", "sprints", ["sprint_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_tasks_sprint_id", "tasks", ["sprint_id"])

    seed_sprint_status_options()


def downgrade() -> None:
    op.drop_index("ix_tasks_sprint_id", table_name="tasks")
    op.drop_constraint("fk_tasks_sprint_id_sprints", "tasks", type_="foreignkey")
    op.drop_column("tasks", "sprint_id")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("ix_sprints_goal_trgm", table_name="sprints")
        op.drop_index("ix_sprints_name_trgm", table_name="sprints")
    op.drop_index("ix_sprints_account_id_project_id", table_name="sprints")
    op.drop_table("sprints")
    op.drop_constraint("ck_projects_delivery_type", "projects", type_="check")
    op.drop_column("projects", "delivery_type")


def seed_sprint_status_options() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    accounts = sa.Table("accounts", metadata, sa.Column("id", sa.Uuid(as_uuid=True)))
    option_sets = sa.Table(
        "option_sets",
        metadata,
        sa.Column("id", sa.Uuid(as_uuid=True)),
        sa.Column("account_id", sa.Uuid(as_uuid=True)),
        sa.Column("entity_type", sa.String(length=100)),
        sa.Column("name", sa.String(length=100)),
        sa.Column("is_system", sa.Boolean()),
    )
    option_values = sa.Table(
        "option_values",
        metadata,
        sa.Column("id", sa.Uuid(as_uuid=True)),
        sa.Column("option_set_id", sa.Uuid(as_uuid=True)),
        sa.Column("label", sa.String(length=255)),
        sa.Column("value", sa.String(length=100)),
        sa.Column("sort_order", sa.Integer()),
        sa.Column("is_default", sa.Boolean()),
    )

    for account_id in bind.execute(sa.select(accounts.c.id)).scalars().all():
        existing_option_set_id = bind.execute(
            sa.select(option_sets.c.id).where(
                option_sets.c.account_id == account_id,
                option_sets.c.entity_type == "SPRINT",
                option_sets.c.name == "STATUS",
            )
        ).scalar_one_or_none()
        if existing_option_set_id is not None:
            continue
        option_set_id = uuid4()
        bind.execute(
            option_sets.insert().values(
                id=option_set_id,
                account_id=account_id,
                entity_type="SPRINT",
                name="STATUS",
                is_system=True,
            )
        )
        for sort_order, (label, value) in enumerate(
            (
                ("Planned", "PLANNED"),
                ("Active", "ACTIVE"),
                ("Completed", "COMPLETED"),
                ("Cancelled", "CANCELLED"),
            )
        ):
            bind.execute(
                option_values.insert().values(
                    id=uuid4(),
                    option_set_id=option_set_id,
                    label=label,
                    value=value,
                    sort_order=sort_order,
                    is_default=sort_order == 0,
                )
            )