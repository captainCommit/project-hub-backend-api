"""retire the decision statuses the app never offered

Decisions offer Draft, Open, In Review, Approved, Cancelled and Deferred. The API's defaults also had Pending, Rejected
and Superseded, which no screen let you pick. Decisions on them move to Open (Pending) or Cancelled (Rejected,
Superseded), the three are switched off in every account's decision status list, and the rest are put in the order
the app shows them (they were seeded with the retired ones in between, so Approved came before Open).

Revision ID: 202610260400
Revises: 202610260300
Create Date: 2026-10-26 04:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "202610260400"
down_revision: Union[str, Sequence[str], None] = "202610260300"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MOVES = {"PENDING": "OPEN", "REJECTED": "CANCELLED", "SUPERSEDED": "CANCELLED"}
ORDER = ["DRAFT", "OPEN", "IN_REVIEW", "APPROVED", "CANCELLED", "DEFERRED"]
DECISION_STATUS_SETS = "SELECT id FROM option_sets WHERE entity_type = 'DECISION' AND name = 'STATUS'"


def upgrade() -> None:
    for old, new in MOVES.items():
        op.execute(
            f"""
            UPDATE decisions d SET status_id = target.id
            FROM option_values retired
            JOIN option_values target ON target.option_set_id = retired.option_set_id AND target.value = '{new}'
            WHERE d.status_id = retired.id AND retired.value = '{old}'
              AND retired.option_set_id IN ({DECISION_STATUS_SETS})
            """
        )
    op.execute(
        f"""
        UPDATE option_values SET is_active = false, is_default = false
        WHERE value IN ('PENDING', 'REJECTED', 'SUPERSEDED') AND option_set_id IN ({DECISION_STATUS_SETS})
        """
    )
    for position, value in enumerate(ORDER):
        op.execute(
            f"UPDATE option_values SET sort_order = {position} "
            f"WHERE value = '{value}' AND option_set_id IN ({DECISION_STATUS_SETS})"
        )
    # A set whose default was one of them gets Draft back as its default.
    op.execute(
        f"""
        UPDATE option_values SET is_default = true
        WHERE value = 'DRAFT' AND option_set_id IN ({DECISION_STATUS_SETS})
          AND option_set_id NOT IN (SELECT option_set_id FROM option_values WHERE is_default AND is_active)
        """
    )


def downgrade() -> None:
    # The statuses come back as choices; decisions keep the status they were moved to.
    op.execute(
        f"""
        UPDATE option_values SET is_active = true
        WHERE value IN ('PENDING', 'REJECTED', 'SUPERSEDED') AND option_set_id IN ({DECISION_STATUS_SETS})
        """
    )
