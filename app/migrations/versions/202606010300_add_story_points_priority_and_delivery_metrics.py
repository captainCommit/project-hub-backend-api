"""add story points and task priority

Revision ID: 202606010300
Revises: 202606010230
Create Date: 2026-06-01 03:00:00.000000
"""

from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "202606010300"
down_revision: Union[str, Sequence[str], None] = "202606010230"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("story_points", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("priority_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_check_constraint(
        "ck_tasks_story_points_positive",
        "tasks",
        "story_points IS NULL OR story_points > 0",
    )
    op.create_foreign_key(
        "fk_tasks_priority_id_option_values",
        "tasks",
        "option_values",
        ["priority_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_tasks_priority_id", "tasks", ["priority_id"])
    seed_task_priority_options()


def downgrade() -> None:
    op.drop_index("ix_tasks_priority_id", table_name="tasks")
    op.drop_constraint("fk_tasks_priority_id_option_values", "tasks", type_="foreignkey")
    op.drop_constraint("ck_tasks_story_points_positive", "tasks", type_="check")
    op.drop_column("tasks", "priority_id")
    op.drop_column("tasks", "story_points")


def seed_task_priority_options() -> None:
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
        option_set_id = bind.execute(
            sa.select(option_sets.c.id).where(
                option_sets.c.account_id == account_id,
                option_sets.c.entity_type == "TASK",
                option_sets.c.name == "PRIORITY",
            )
        ).scalar_one_or_none()
        if option_set_id is None:
            option_set_id = uuid4()
            bind.execute(
                option_sets.insert().values(
                    id=option_set_id,
                    account_id=account_id,
                    entity_type="TASK",
                    name="PRIORITY",
                    is_system=True,
                )
            )
        existing_values = set(
            bind.execute(
                sa.select(option_values.c.value).where(option_values.c.option_set_id == option_set_id)
            ).scalars().all()
        )
        has_default = bool(
            bind.execute(
                sa.select(option_values.c.id)
                .where(
                    option_values.c.option_set_id == option_set_id,
                    option_values.c.is_default.is_(True),
                )
                .limit(1)
            ).scalar_one_or_none()
        )
        for sort_order, (label, value) in enumerate(
            (
                ("Low", "LOW"),
                ("Medium", "MEDIUM"),
                ("High", "HIGH"),
                ("Critical", "CRITICAL"),
            )
        ):
            if value in existing_values:
                continue
            bind.execute(
                option_values.insert().values(
                    id=uuid4(),
                    option_set_id=option_set_id,
                    label=label,
                    value=value,
                    sort_order=sort_order,
                    is_default=sort_order == 0 and not has_default,
                )
            )
            if sort_order == 0 and not has_default:
                has_default = True