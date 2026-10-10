"""allow the D MMM YYYY account date format

The UI offers "D MMM YYYY" (e.g. 5 Oct 2026) as a date format setting.

Revision ID: 202610100100
Revises: 202610090100
Create Date: 2026-10-10 01:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "202610100100"
down_revision: Union[str, Sequence[str], None] = "202610090100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "ck_account_settings_date_format"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "account_settings", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "account_settings",
        "date_format in ('MM/dd/yyyy', 'dd/MM/yyyy', 'yyyy-MM-dd', 'D MMM YYYY')",
    )


def downgrade() -> None:
    op.execute("UPDATE account_settings SET date_format = 'MM/dd/yyyy' WHERE date_format = 'D MMM YYYY'")
    op.drop_constraint(CONSTRAINT, "account_settings", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "account_settings",
        "date_format in ('MM/dd/yyyy', 'dd/MM/yyyy', 'yyyy-MM-dd')",
    )
