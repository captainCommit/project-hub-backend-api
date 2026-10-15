"""link every account member to a resource

Each member gets a resource linked to their login, so work assigned to them counts as theirs. An existing
unlinked resource with the same name is linked rather than duplicated.

Revision ID: 202610150500
Revises: 202610150400
Create Date: 2026-10-15 05:00:00.000000
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision: str = "202610150500"
down_revision: Union[str, Sequence[str], None] = "202610150400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    members = bind.execute(sa.text(
        """
        SELECT m.account_id, u.id AS user_id, u.full_name, u.email
        FROM account_members m
        JOIN users u ON u.id = m.user_id
        WHERE NOT EXISTS (
            SELECT 1 FROM resources r WHERE r.account_id = m.account_id AND r.user_id = u.id
        )
        ORDER BY m.created_at
        """
    )).all()
    for account_id, user_id, full_name, email in members:
        name = " ".join((full_name or "").split()) or email.split("@")[0]
        match = bind.execute(sa.text(
            """
            SELECT id FROM resources
            WHERE account_id = :account_id AND user_id IS NULL AND is_active
              AND lower(trim(name)) = :name
            ORDER BY created_at, id
            LIMIT 1
            """
        ), {"account_id": account_id, "name": name.lower()}).scalar()
        if match is not None:
            bind.execute(sa.text("UPDATE resources SET user_id = :user_id WHERE id = :id"), {"user_id": user_id, "id": match})
        else:
            bind.execute(sa.text(
                "INSERT INTO resources (id, account_id, user_id, name) VALUES (:id, :account_id, :user_id, :name)"
            ), {"id": uuid4(), "account_id": account_id, "user_id": user_id, "name": name})


def downgrade() -> None:
    # Links and resources made here can't be told apart from ones people made, so nothing is undone.
    pass
