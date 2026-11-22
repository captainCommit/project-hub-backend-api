"""task types: add Phase and Deliverable to every account's task types

The sheet, board and Gantt already draw these two types, but accounts were only given Work Package, Milestone and
Activity. They're added after the account's existing types; an account that already has one (even switched off)
keeps it as it is.

Revision ID: 202611210700
Revises: 202611210600
Create Date: 2026-11-21 07:00:00.000000
"""

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202611210700"
down_revision: Union[str, Sequence[str], None] = "202611210600"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TYPES = (("PHASE", "Phase"), ("DELIVERABLE", "Deliverable"))


def upgrade() -> None:
    bind = op.get_bind()
    sets = bind.execute(sa.text("select id from option_sets where entity_type = 'TASK' and name = 'TYPE'")).scalars().all()
    for set_id in sets:
        existing = {
            value.upper()
            for value in bind.execute(
                sa.text("select value from option_values where option_set_id = :set_id"), {"set_id": set_id}
            ).scalars()
        }
        next_order = bind.execute(
            sa.text("select coalesce(max(sort_order), 0) from option_values where option_set_id = :set_id"),
            {"set_id": set_id},
        ).scalar_one()
        for value, label in NEW_TYPES:
            if value in existing:
                continue
            next_order += 1
            bind.execute(
                sa.text(
                    "insert into option_values (id, option_set_id, label, value, sort_order, is_active, is_default)"
                    " values (:id, :set_id, :label, :value, :sort_order, true, false)"
                ),
                {"id": uuid.uuid4(), "set_id": set_id, "label": label, "value": value, "sort_order": next_order},
            )


def downgrade() -> None:
    # Tasks may already use these types; leave them in place.
    pass
