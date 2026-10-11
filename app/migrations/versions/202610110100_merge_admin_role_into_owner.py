"""merge the ADMIN account role into OWNER

An account's owners are its admins; there is no separate ADMIN role.
The downgrade restores the constraint only: former admins stay owners.

Revision ID: 202610110100
Revises: 202610100200
Create Date: 2026-10-11 01:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "202610110100"
down_revision: Union[str, Sequence[str], None] = "202610100200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE account_members SET role = 'OWNER' WHERE role = 'ADMIN'")
    op.drop_constraint("ck_account_members_role", "account_members", type_="check")
    op.create_check_constraint(
        "ck_account_members_role",
        "account_members",
        "role in ('OWNER', 'MANAGER', 'MEMBER', 'VIEWER')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_account_members_role", "account_members", type_="check")
    op.create_check_constraint(
        "ck_account_members_role",
        "account_members",
        "role in ('OWNER', 'ADMIN', 'MANAGER', 'MEMBER', 'VIEWER')",
    )
