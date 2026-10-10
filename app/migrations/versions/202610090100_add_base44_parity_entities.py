"""add entities needed to retire Base44

Adds dependencies, change requests, requirements, test cases, shared access,
templates and support tickets; resource/project/user profile columns; hours on task
assignments; free-text person names on RAID entries; decision priority and links;
and the option sets those entities use.

Revision ID: 202610090100
Revises: 202606011000
Create Date: 2026-10-09 01:00:00.000000
"""

from typing import Sequence, Union
from uuid import uuid4
import re

from alembic import op
import sqlalchemy as sa


revision: str = "202610090100"
down_revision: Union[str, Sequence[str], None] = "202606011000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRIORITY_VALUES = ("Low", "Medium", "High", "Critical")

# Frozen copy of the option sets introduced or extended by this revision.
OPTION_SETS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("RISK", "STATUS", ("Open", "Mitigating", "Closed", "Resolved", "Blocked", "Escalated", "Cancelled")),
    ("ISSUE", "STATUS", ("Open", "In Progress", "Resolved", "Closed", "Blocked", "Escalated", "Cancelled")),
    ("DECISION", "PRIORITY", PRIORITY_VALUES),
    (
        "DECISION",
        "STATUS",
        ("Draft", "Pending", "Approved", "Rejected", "Superseded", "Open", "In Review", "Cancelled", "Deferred"),
    ),
    ("ASSUMPTION", "STATUS", ("Draft", "Validated", "Invalidated", "Open", "In Review", "Closed")),
    ("DEPENDENCY", "PRIORITY", PRIORITY_VALUES),
    ("DEPENDENCY", "STATUS", ("Open", "In Review", "Closed", "Escalated")),
    ("CHANGE_REQUEST", "PRIORITY", PRIORITY_VALUES),
    ("CHANGE_REQUEST", "STATUS", ("Draft", "Open", "In Review", "Approved", "Cancelled", "Deferred", "Implemented")),
    ("REQUIREMENT", "PRIORITY", PRIORITY_VALUES),
    ("REQUIREMENT", "STATUS", ("In Review", "Validated", "Deferred", "Cancelled")),
    ("TEST_CASE", "PRIORITY", PRIORITY_VALUES),
)


def fk_column(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Uuid(as_uuid=True), nullable=nullable)


def timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def create_project_log_table(table: str, number_field: str, extra_columns: list[sa.Column], extra_fks: list) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        fk_column("account_id", nullable=False),
        fk_column("project_id", nullable=False),
        fk_column("program_id"),
        sa.Column(number_field, sa.String(length=50), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        fk_column("priority_id"),
        fk_column("status_id"),
        *extra_columns,
        fk_column("created_by"),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["program_id"], ["programs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["priority_id"], ["option_values.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["status_id"], ["option_values.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        *extra_fks,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", number_field, name=f"uq_{table}_project_id_{number_field}"),
    )
    op.create_index(f"ix_{table}_account_id", table, ["account_id"])
    op.create_index(f"ix_{table}_project_id", table, ["project_id"])


def upgrade() -> None:
    op.add_column("resources", sa.Column("department", sa.String(length=255), nullable=True))
    op.add_column("resources", sa.Column("hourly_rate", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "resources",
        sa.Column("non_project_hours", sa.Numeric(10, 2), server_default="0", nullable=False),
    )
    op.add_column("projects", sa.Column("budget", sa.Numeric(14, 2), nullable=True))
    op.add_column("task_assignments", sa.Column("hours", sa.Numeric(10, 2), nullable=True))
    op.add_column("comments", sa.Column("parent_comment_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_comments_parent_comment_id_comments",
        "comments",
        "comments",
        ["parent_comment_id"],
        ["id"],
        ondelete="CASCADE",
    )
    for column in ("first_name", "last_name", "title", "department", "company"):
        op.add_column("users", sa.Column(column, sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("photo_s3_key", sa.String(length=512), nullable=True))

    for table in ("risks", "issues"):
        op.add_column(table, sa.Column("assigned_to_name", sa.String(length=255), nullable=True))
    op.add_column("assumptions", sa.Column("entered_by_name", sa.String(length=255), nullable=True))
    op.add_column("decisions", sa.Column("priority_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column("decisions", sa.Column("proposed_by_name", sa.String(length=255), nullable=True))
    op.add_column("decisions", sa.Column("related_record", sa.String(length=255), nullable=True))
    op.create_foreign_key(
        "fk_decisions_priority_id_option_values",
        "decisions",
        "option_values",
        ["priority_id"],
        ["id"],
        ondelete="SET NULL",
    )

    create_project_log_table(
        "dependencies",
        "dependency_number",
        [
            sa.Column("impact_of_delay", sa.Text(), nullable=True),
            sa.Column("fallback_plan", sa.Text(), nullable=True),
            sa.Column("assigned_to_name", sa.String(length=255), nullable=True),
            sa.Column("entered_by_name", sa.String(length=255), nullable=True),
            sa.Column("date_entered", sa.Date(), nullable=True),
        ],
        [],
    )
    create_project_log_table(
        "change_requests",
        "change_request_number",
        [
            sa.Column("proposed_by_name", sa.String(length=255), nullable=True),
            sa.Column("proposed_date", sa.Date(), nullable=True),
            sa.Column("related_record", sa.String(length=255), nullable=True),
        ],
        [],
    )
    create_project_log_table(
        "requirements",
        "requirement_number",
        [
            sa.Column("entered_by_name", sa.String(length=255), nullable=True),
            sa.Column("entered_date", sa.Date(), nullable=True),
        ],
        [],
    )
    create_project_log_table(
        "test_cases",
        "test_case_number",
        [
            fk_column("sprint_id"),
            fk_column("requirement_id"),
            sa.Column("designed_by_name", sa.String(length=255), nullable=True),
            sa.Column("executed_by_name", sa.String(length=255), nullable=True),
            sa.Column("execution_date", sa.Date(), nullable=True),
            sa.Column("result", sa.String(length=20), server_default="NOT_EXECUTED", nullable=False),
            sa.Column("dependencies", sa.Text(), nullable=True),
            sa.Column("test_steps", sa.Text(), nullable=True),
            sa.Column("conditions", sa.Text(), nullable=True),
            sa.Column("test_input", sa.Text(), nullable=True),
            sa.Column("expected_results", sa.Text(), nullable=True),
            sa.Column("actual_results", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
        ],
        [
            sa.ForeignKeyConstraint(["sprint_id"], ["sprints.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"], ondelete="SET NULL"),
        ],
    )

    op.create_table(
        "shared_access",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        fk_column("account_id", nullable=False),
        sa.Column("item_type", sa.String(length=20), nullable=False),
        sa.Column("item_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("shared_with_email", sa.String(length=255), nullable=False),
        sa.Column("access_level", sa.String(length=10), server_default="VIEW", nullable=False),
        fk_column("created_by"),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_type", "item_id", "shared_with_email", name="uq_shared_access_item_email"),
        sa.CheckConstraint("item_type in ('PORTFOLIO', 'PROGRAM', 'PROJECT')", name="ck_shared_access_item_type"),
        sa.CheckConstraint("access_level in ('VIEW', 'EDIT')", name="ck_shared_access_access_level"),
    )
    op.create_index("ix_shared_access_item", "shared_access", ["item_type", "item_id"])

    op.create_table(
        "templates",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        fk_column("account_id", nullable=False),
        sa.Column("template_type", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=50), nullable=True),
        sa.Column("content", sa.JSON(), nullable=False),
        fk_column("created_by"),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("template_type in ('PORTFOLIO', 'PROGRAM', 'PROJECT')", name="ck_templates_template_type"),
    )
    op.create_index("ix_templates_account_id_template_type", "templates", ["account_id", "template_type"])

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        fk_column("account_id", nullable=False),
        sa.Column("ticket_number", sa.String(length=50), nullable=False),
        sa.Column("category", sa.String(length=30), server_default="GENERAL", nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=10), server_default="MEDIUM", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="OPEN", nullable=False),
        fk_column("requester_user_id"),
        sa.Column("requester_name", sa.String(length=255), nullable=False),
        sa.Column("requester_email", sa.String(length=255), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "ticket_number", name="uq_support_tickets_account_id_ticket_number"),
        sa.CheckConstraint(
            "category in ('GENERAL', 'TECHNICAL', 'BILLING', 'BUG', 'FEATURE_REQUEST', 'ACCOUNT')",
            name="ck_support_tickets_category",
        ),
        sa.CheckConstraint("priority in ('LOW', 'MEDIUM', 'HIGH')", name="ck_support_tickets_priority"),
        sa.CheckConstraint(
            "status in ('OPEN', 'AWAITING_RESPONSE', 'IN_PROGRESS', 'RESOLVED', 'CLOSED')",
            name="ck_support_tickets_status",
        ),
    )

    seed_option_sets()


def downgrade() -> None:
    op.drop_table("support_tickets")
    op.drop_index("ix_templates_account_id_template_type", table_name="templates")
    op.drop_table("templates")
    op.drop_index("ix_shared_access_item", table_name="shared_access")
    op.drop_table("shared_access")
    for table in ("test_cases", "requirements", "change_requests", "dependencies"):
        op.drop_index(f"ix_{table}_project_id", table_name=table)
        op.drop_index(f"ix_{table}_account_id", table_name=table)
        op.drop_table(table)
    op.drop_constraint("fk_decisions_priority_id_option_values", "decisions", type_="foreignkey")
    for column in ("related_record", "proposed_by_name", "priority_id"):
        op.drop_column("decisions", column)
    op.drop_column("assumptions", "entered_by_name")
    for table in ("issues", "risks"):
        op.drop_column(table, "assigned_to_name")
    for column in ("photo_s3_key", "phone", "company", "department", "title", "last_name", "first_name"):
        op.drop_column("users", column)
    op.drop_constraint("fk_comments_parent_comment_id_comments", "comments", type_="foreignkey")
    op.drop_column("comments", "parent_comment_id")
    op.drop_column("task_assignments", "hours")
    op.drop_column("projects", "budget")
    for column in ("non_project_hours", "hourly_rate", "department"):
        op.drop_column("resources", column)


def normalize_key(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", label.strip()).strip("_").upper()


def seed_option_sets() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    accounts = sa.Table("accounts", metadata, sa.Column("id", sa.Uuid(as_uuid=True)))
    option_sets = sa.Table(
        "option_sets",
        metadata,
        sa.Column("id", sa.Uuid(as_uuid=True)),
        sa.Column("account_id", sa.Uuid(as_uuid=True)),
        sa.Column("entity_type", sa.String(length=100)),
        sa.Column("name", sa.String(length=100)),
        sa.Column("is_system", sa.Boolean()),
    )
    option_values = sa.Table(
        "option_values",
        metadata,
        sa.Column("id", sa.Uuid(as_uuid=True)),
        sa.Column("option_set_id", sa.Uuid(as_uuid=True)),
        sa.Column("label", sa.String(length=255)),
        sa.Column("value", sa.String(length=100)),
        sa.Column("sort_order", sa.Integer()),
        sa.Column("is_default", sa.Boolean()),
    )

    for account_id in bind.execute(sa.select(accounts.c.id)).scalars().all():
        for entity_type, name, labels in OPTION_SETS:
            option_set_id = bind.execute(
                sa.select(option_sets.c.id).where(
                    option_sets.c.account_id == account_id,
                    option_sets.c.entity_type == entity_type,
                    option_sets.c.name == name,
                )
            ).scalar_one_or_none()
            if option_set_id is None:
                option_set_id = uuid4()
                bind.execute(
                    option_sets.insert().values(
                        id=option_set_id,
                        account_id=account_id,
                        entity_type=entity_type,
                        name=name,
                        is_system=True,
                    )
                )
            existing_values = set(
                bind.execute(
                    sa.select(option_values.c.value).where(option_values.c.option_set_id == option_set_id)
                ).scalars().all()
            )
            has_default = bool(
                bind.execute(
                    sa.select(option_values.c.id)
                    .where(option_values.c.option_set_id == option_set_id, option_values.c.is_default.is_(True))
                    .limit(1)
                ).scalar_one_or_none()
            )
            next_sort_order = len(existing_values)
            for label in labels:
                value = normalize_key(label)
                if value in existing_values:
                    continue
                bind.execute(
                    option_values.insert().values(
                        id=uuid4(),
                        option_set_id=option_set_id,
                        label=label,
                        value=value,
                        sort_order=next_sort_order,
                        is_default=not has_default,
                    )
                )
                existing_values.add(value)
                next_sort_order += 1
                has_default = True
