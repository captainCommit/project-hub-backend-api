"""add skills competency matrix

Revision ID: 202606011000
Revises: 202606010300
Create Date: 2026-06-01 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202606011000"
down_revision: Union[str, Sequence[str], None] = "202606010300"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_skills_account_id", "skills", ["account_id"])
    op.create_index("ix_skills_account_id_is_active", "skills", ["account_id", "is_active"])

    op.create_table(
        "resource_skills",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resource_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("skill_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("proficiency", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "proficiency in ('BEGINNER', 'INTERMEDIATE', 'ADVANCED', 'EXPERT')",
            name="ck_resource_skills_proficiency",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_id", "skill_id", name="uq_resource_skills_resource_id_skill_id"),
    )
    op.create_index(
        "ix_resource_skills_account_id_resource_id",
        "resource_skills",
        ["account_id", "resource_id"],
    )
    op.create_index(
        "ix_resource_skills_account_id_skill_id",
        "resource_skills",
        ["account_id", "skill_id"],
    )

    op.create_table(
        "task_required_skills",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("skill_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("required_proficiency", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "required_proficiency is null or required_proficiency in ('BEGINNER', 'INTERMEDIATE', 'ADVANCED', 'EXPERT')",
            name="ck_task_required_skills_required_proficiency",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "skill_id", name="uq_task_required_skills_task_id_skill_id"),
    )
    op.create_index(
        "ix_task_required_skills_account_id_task_id",
        "task_required_skills",
        ["account_id", "task_id"],
    )
    op.create_index(
        "ix_task_required_skills_account_id_skill_id",
        "task_required_skills",
        ["account_id", "skill_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_required_skills_account_id_skill_id", table_name="task_required_skills")
    op.drop_index("ix_task_required_skills_account_id_task_id", table_name="task_required_skills")
    op.drop_table("task_required_skills")
    op.drop_index("ix_resource_skills_account_id_skill_id", table_name="resource_skills")
    op.drop_index("ix_resource_skills_account_id_resource_id", table_name="resource_skills")
    op.drop_table("resource_skills")
    op.drop_index("ix_skills_account_id_is_active", table_name="skills")
    op.drop_index("ix_skills_account_id", table_name="skills")
    op.drop_table("skills")