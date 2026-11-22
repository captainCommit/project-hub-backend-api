"""resources: assigned portfolios, programs and projects, and whether a person is listed on Resources

Resources > Capacity > Add Resource picks an existing member and assigns them to portfolios, programs and projects.
Members joining from now on are not listed until someone adds them; everyone listed today stays listed.
Projects that already had a weekly-hours override become project assignments (with their program and portfolio),
since an override already counted as an assignment for access.

Revision ID: 202611210400
Revises: 202611210300
Create Date: 2026-11-21 04:00:00.000000
"""

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202611210400"
down_revision: Union[str, Sequence[str], None] = "202611210300"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("resources", sa.Column("listed", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.create_table(
        "resource_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resource_id", sa.Uuid(), sa.ForeignKey("resources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=True),
        sa.Column("program_id", sa.Uuid(), sa.ForeignKey("programs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "(case when portfolio_id is null then 0 else 1 end)"
            " + (case when program_id is null then 0 else 1 end)"
            " + (case when project_id is null then 0 else 1 end) = 1",
            name="ck_resource_assignments_one_target",
        ),
        sa.UniqueConstraint("resource_id", "portfolio_id", name="uq_resource_assignments_resource_id_portfolio_id"),
        sa.UniqueConstraint("resource_id", "program_id", name="uq_resource_assignments_resource_id_program_id"),
        sa.UniqueConstraint("resource_id", "project_id", name="uq_resource_assignments_resource_id_project_id"),
    )
    op.create_index("ix_resource_assignments_account_id", "resource_assignments", ["account_id"])
    op.create_index("ix_resource_assignments_project_id", "resource_assignments", ["project_id"])

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "select distinct o.account_id, o.resource_id, p.id, p.program_id, p.portfolio_id"
            " from resource_project_allocations o join projects p on p.id = o.project_id"
        )
    ).all()
    seen: set[tuple[object, str, object]] = set()
    insert = sa.text(
        "insert into resource_assignments (id, account_id, resource_id, portfolio_id, program_id, project_id)"
        " values (:id, :account_id, :resource_id, :portfolio_id, :program_id, :project_id)"
    )
    for account_id, resource_id, project_id, program_id, portfolio_id in rows:
        for column, target in (("project_id", project_id), ("program_id", program_id), ("portfolio_id", portfolio_id)):
            if target is None or (resource_id, column, target) in seen:
                continue
            seen.add((resource_id, column, target))
            values = {"portfolio_id": None, "program_id": None, "project_id": None, column: target}
            bind.execute(insert, {"id": uuid.uuid4(), "account_id": account_id, "resource_id": resource_id, **values})


def downgrade() -> None:
    op.drop_index("ix_resource_assignments_project_id", table_name="resource_assignments")
    op.drop_index("ix_resource_assignments_account_id", table_name="resource_assignments")
    op.drop_table("resource_assignments")
    op.drop_column("resources", "listed")
