"""short RAID and decision numbers: RISK-/ISSUE-/ASS-/DEC- become R-/I-/A-/D-

Revision ID: 202611100100
Revises: 202611090100
Create Date: 2026-11-10 01:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "202611100100"
down_revision: Union[str, Sequence[str], None] = "202611090100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# table, number column, old prefix, new prefix
RENAMES = (
    ("risks", "risk_number", "RISK-", "R-"),
    ("issues", "issue_number", "ISSUE-", "I-"),
    ("assumptions", "assumption_number", "ASS-", "A-"),
    ("decisions", "decision_number", "DEC-", "D-"),
)


def upgrade() -> None:
    for table, column, old, new in RENAMES:
        # RISK-003 becomes R-003. A project that already has that short number keeps its old one rather than failing.
        op.execute(
            f"""
            UPDATE {table}
            SET {column} = '{new}' || substr({column}, {len(old) + 1})
            WHERE {column} LIKE '{old}%'
              AND NOT EXISTS (
                SELECT 1 FROM {table} AS taken
                WHERE taken.project_id = {table}.project_id
                  AND taken.{column} = '{new}' || substr({table}.{column}, {len(old) + 1})
              )
            """
        )
        # Decisions and change requests name the records they relate to by number, e.g. ["DEC-001"].
        for linking in ("decisions", "change_requests"):
            op.execute(
                f"UPDATE {linking} SET related_record = REPLACE(related_record, '{old}', '{new}') "
                f"WHERE related_record LIKE '%{old}%'"
            )


def downgrade() -> None:
    # Not reversed: many records were created with the short numbers before this migration (the seed data uses
    # R-/I-/A-/D-), so there is no way to tell which ones were renamed. The short numbers work with the old code too.
    pass
