"""users & roles: account-defined roles with a permission grid replace manager/member/viewer grants

Each account gets the built-in roles; every member moves to the closest one:
OWNER -> Owner, VIEWER -> Viewer, MEMBER -> Contributor, MANAGER -> Portfolio, Program or Project Manager after
the widest place they held a manager grant. Access grants stay as the record of who is assigned where; their
role and module columns are no longer used for permissions. A MANAGER seat becomes a full MEMBER seat.

Revision ID: 202611210300
Revises: 202611210200
Create Date: 2026-11-21 03:00:00.000000
"""

import json
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202611210300"
down_revision: Union[str, Sequence[str], None] = "202611210200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# A copy of app/services/role_defaults.py as of this migration (migrations must not change if that file does).
DEFAULT_ROLES = [{'key': 'OWNER',
  'name': 'Owner',
  'description': 'Full control of the account: everything in every project, users, roles, settings and billing.',
  'scope': 'ALL',
  'permissions': {'PORTFOLIOS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'PROGRAMS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'PROJECTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'REQUIREMENTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'TESTING': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'SPRINTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'RAID': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'GOVERNANCE': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'RESOURCES': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'REPORTING': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE']}},
 {'key': 'PORTFOLIO_MANAGER',
  'name': 'Portfolio Manager',
  'description': 'Runs assigned portfolios: their programs, projects and all delivery work.',
  'scope': 'ASSIGNED',
  'permissions': {'PORTFOLIOS': ['VIEW', 'EDIT'],
                  'PROGRAMS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'PROJECTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'REQUIREMENTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'TESTING': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'SPRINTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'RAID': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'GOVERNANCE': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'RESOURCES': ['VIEW', 'CREATE', 'EDIT'],
                  'REPORTING': ['VIEW']}},
 {'key': 'PROGRAM_MANAGER',
  'name': 'Program Manager',
  'description': 'Runs assigned programs: their projects and all delivery work.',
  'scope': 'ASSIGNED',
  'permissions': {'PORTFOLIOS': ['VIEW'],
                  'PROGRAMS': ['VIEW', 'EDIT'],
                  'PROJECTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'REQUIREMENTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'TESTING': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'SPRINTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'RAID': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'GOVERNANCE': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'RESOURCES': ['VIEW', 'CREATE', 'EDIT'],
                  'REPORTING': ['VIEW']}},
 {'key': 'PROJECT_MANAGER',
  'name': 'Project Manager',
  'description': 'Manages assigned projects, including planning, tasks, resources, and RAID.',
  'scope': 'ASSIGNED',
  'permissions': {'PORTFOLIOS': ['VIEW'],
                  'PROGRAMS': ['VIEW'],
                  'PROJECTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE'],
                  'REQUIREMENTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'TESTING': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'SPRINTS': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'RAID': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'GOVERNANCE': ['VIEW', 'CREATE', 'EDIT', 'DELETE', 'APPROVE'],
                  'RESOURCES': ['VIEW', 'EDIT'],
                  'REPORTING': ['VIEW']}},
 {'key': 'CONTRIBUTOR',
  'name': 'Contributor',
  'description': 'Works on assigned projects: updates tasks, RAID items and delivery records.',
  'scope': 'ASSIGNED',
  'permissions': {'PORTFOLIOS': ['VIEW'],
                  'PROGRAMS': ['VIEW'],
                  'PROJECTS': ['VIEW', 'EDIT'],
                  'REQUIREMENTS': ['VIEW', 'CREATE', 'EDIT'],
                  'TESTING': ['VIEW', 'CREATE', 'EDIT'],
                  'SPRINTS': ['VIEW', 'CREATE', 'EDIT'],
                  'RAID': ['VIEW', 'CREATE', 'EDIT'],
                  'GOVERNANCE': ['VIEW', 'CREATE', 'EDIT'],
                  'RESOURCES': ['VIEW'],
                  'REPORTING': ['VIEW']}},
 {'key': 'VIEWER',
  'name': 'Viewer',
  'description': 'Read-only access to assigned projects.',
  'scope': 'ASSIGNED',
  'permissions': {'PORTFOLIOS': ['VIEW'],
                  'PROGRAMS': ['VIEW'],
                  'PROJECTS': ['VIEW'],
                  'REQUIREMENTS': ['VIEW'],
                  'TESTING': ['VIEW'],
                  'SPRINTS': ['VIEW'],
                  'RAID': ['VIEW'],
                  'GOVERNANCE': ['VIEW'],
                  'RESOURCES': ['VIEW'],
                  'REPORTING': ['VIEW']}}]


def upgrade() -> None:
    op.create_table(
        "account_roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("scope", sa.String(length=20), server_default="ASSIGNED", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="100", nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("scope in ('ASSIGNED', 'ALL')", name="ck_account_roles_scope"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "name", name="uq_account_roles_account_id_name"),
        sa.UniqueConstraint("account_id", "key", name="uq_account_roles_account_id_key"),
    )
    op.add_column("account_members", sa.Column("role_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_account_members_role_id_account_roles", "account_members", "account_roles", ["role_id"], ["id"], ondelete="SET NULL"
    )
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    insert_role = sa.text(
        "INSERT INTO account_roles (id, account_id, key, name, description, permissions, scope, sort_order, is_system) "
        "VALUES (:id, :account_id, :key, :name, :description, :permissions, :scope, :sort_order, true)"
    )
    for (account_id,) in bind.execute(sa.text("SELECT id FROM accounts")).fetchall():
        ids = {}
        for order, role in enumerate(DEFAULT_ROLES):
            ids[role["key"]] = uuid.uuid4()
            bind.execute(insert_role, {
                "id": ids[role["key"]], "account_id": account_id, "key": role["key"], "name": role["name"],
                "description": role["description"], "permissions": json.dumps(role["permissions"]),
                "scope": role["scope"], "sort_order": order,
            })
        members = bind.execute(
            sa.text("SELECT id, user_id, role FROM account_members WHERE account_id = :account_id"), {"account_id": account_id}
        ).fetchall()
        for member_id, user_id, role in members:
            if role == "OWNER":
                key, seat = "OWNER", "OWNER"
            elif role == "VIEWER":
                key, seat = "VIEWER", "VIEWER"
            elif role == "MEMBER":
                key, seat = "CONTRIBUTOR", "MEMBER"
            else:
                levels = bind.execute(sa.text(
                    "SELECT portfolio_id IS NOT NULL, program_id IS NOT NULL FROM access_grants "
                    "WHERE account_id = :account_id AND user_id = :user_id AND role = 'MANAGER'"
                ), {"account_id": account_id, "user_id": user_id}).fetchall()
                key = (
                    "PORTFOLIO_MANAGER" if any(p for p, _ in levels)
                    else "PROGRAM_MANAGER" if any(g for _, g in levels)
                    else "PROJECT_MANAGER"
                )
                seat = "MEMBER"
            bind.execute(
                sa.text("UPDATE account_members SET role_id = :role_id, role = :seat WHERE id = :id"),
                {"role_id": ids[key], "seat": seat, "id": member_id},
            )


def downgrade() -> None:
    op.drop_constraint("fk_account_members_role_id_account_roles", "account_members", type_="foreignkey")
    op.drop_column("account_members", "role_id")
    op.drop_table("account_roles")
