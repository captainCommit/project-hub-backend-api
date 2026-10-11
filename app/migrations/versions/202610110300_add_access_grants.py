"""add access_grants (per portfolio / program / project roles)

Non-owners now see only what they are granted. To keep today's access, every existing
manager, member and viewer gets a grant on every existing portfolio at their account role;
members get every module.

Revision ID: 202610110300
Revises: 202610110200
Create Date: 2026-10-11 03:00:00.000000
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "202610110300"
down_revision: Union[str, Sequence[str], None] = "202610110200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The module list as of this migration; later modules are not backfilled.
ALL_MODULES = [
    "TASKS",
    "SPRINTS",
    "RISKS",
    "ISSUES",
    "ASSUMPTIONS",
    "DECISIONS",
    "DEPENDENCIES",
    "CHANGE_REQUESTS",
    "REQUIREMENTS",
    "TESTING",
    "FINANCIALS",
]


def upgrade() -> None:
    op.create_table(
        "access_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=True),
        sa.Column("program_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("modules", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role in ('MANAGER', 'MEMBER', 'VIEWER')", name="ck_access_grants_role"),
        sa.CheckConstraint(
            "(CASE WHEN portfolio_id IS NULL THEN 0 ELSE 1 END"
            " + CASE WHEN program_id IS NULL THEN 0 ELSE 1 END"
            " + CASE WHEN project_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_access_grants_one_scope",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["program_id"], ["programs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "portfolio_id", name="uq_access_grants_user_portfolio"),
        sa.UniqueConstraint("user_id", "program_id", name="uq_access_grants_user_program"),
        sa.UniqueConstraint("user_id", "project_id", name="uq_access_grants_user_project"),
    )
    op.create_index("ix_access_grants_account_id_user_id", "access_grants", ["account_id", "user_id"])

    op.execute(
        sa.text(
            """
            INSERT INTO access_grants (id, account_id, user_id, portfolio_id, role, modules)
            SELECT gen_random_uuid(), m.account_id, m.user_id, p.id, m.role,
                   CASE WHEN m.role = 'MEMBER' THEN CAST(:modules AS json) END
            FROM account_members m
            JOIN portfolios p ON p.account_id = m.account_id
            WHERE m.role IN ('MANAGER', 'MEMBER', 'VIEWER')
            """
        ).bindparams(modules=json.dumps(ALL_MODULES))
    )


def downgrade() -> None:
    op.drop_index("ix_access_grants_account_id_user_id", table_name="access_grants")
    op.drop_table("access_grants")
