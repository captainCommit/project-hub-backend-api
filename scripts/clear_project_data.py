"""Delete all portfolio, program and project data from every account, keeping users and account setup.

Deleted: portfolios, programs, projects and everything under them (tasks, sprints, assignments, dependencies,
RAID, change requests, decisions, requirements, test cases, project access grants, Slack channel mappings), plus
the comments, attachments (rows and S3 files), activity, notifications, share links and AI reports that point at
them, and resources that aren't linked to a user (contractors, vendors, placeholders).

Kept: users, accounts, memberships and roles, account settings and option lists, member resources and their time
off, skills, holidays, templates, billing, integrations, AI settings and usage, and support tickets.

Without --yes this is a dry run: it does the deletes inside a transaction, prints what would go, and rolls back.
Run from project-hub-api (uses DATABASE_URL from .env unless --database-url is given):
    python scripts/clear_project_data.py          # preview
    python scripts/clear_project_data.py --yes    # delete
Take an RDS snapshot first; there is no undo.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, create_database_engine, engine as app_engine  # noqa: E402
import app.models  # noqa: E402,F401  (registers every table on Base.metadata)

# Every record that goes, so polymorphic rows (comments, attachments, activity...) can be matched by id.
DOOMED_IDS = """
    INSERT INTO doomed_ids
    SELECT id FROM portfolios UNION SELECT id FROM programs UNION SELECT id FROM projects
    UNION SELECT id FROM tasks UNION SELECT id FROM sprints
    UNION SELECT id FROM risks UNION SELECT id FROM issues UNION SELECT id FROM assumptions
    UNION SELECT id FROM dependencies UNION SELECT id FROM decisions UNION SELECT id FROM decision_options
    UNION SELECT id FROM change_requests UNION SELECT id FROM requirements UNION SELECT id FROM test_cases
    UNION SELECT id FROM resources WHERE user_id IS NULL
"""

DELETES = [
    "DELETE FROM comments WHERE entity_id IN (SELECT id FROM doomed_ids)",
    "DELETE FROM activity_log WHERE entity_id IN (SELECT id FROM doomed_ids)",
    "DELETE FROM notifications WHERE entity_id IN (SELECT id FROM doomed_ids)",
    "DELETE FROM shared_access WHERE item_id IN (SELECT id FROM doomed_ids)",
    "DELETE FROM ai_reports WHERE scope_id IN (SELECT id FROM doomed_ids)",
    "DELETE FROM resources WHERE user_id IS NULL",
    # The rest cascades from these (tasks, sprints, RAID, grants, allocations...).
    "DELETE FROM projects",
    "DELETE FROM programs",
    "DELETE FROM portfolios",
]


def table_counts(db: Session) -> dict[str, int]:
    return {
        table.name: db.execute(text(f'SELECT count(*) FROM "{table.name}"')).scalar_one()
        for table in Base.metadata.sorted_tables
    }


def delete_s3_objects(keys: list[str]) -> None:
    settings = get_settings()
    if not settings.s3_bucket_name:
        print(f"  ! S3_BUCKET_NAME isn't set; {len(keys)} attachment file(s) left in S3.")
        return
    from app.services.attachments import get_s3_client

    s3 = get_s3_client(settings)
    failed = 0
    for start in range(0, len(keys), 1000):
        batch = [{"Key": key} for key in keys[start:start + 1000]]
        result = s3.delete_objects(Bucket=settings.s3_bucket_name, Delete={"Objects": batch, "Quiet": True})
        failed += len(result.get("Errors", []))
    print(f"Deleted {len(keys) - failed} attachment file(s) from s3://{settings.s3_bucket_name}"
          + (f" ({failed} failed)" if failed else ""))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", help="defaults to DATABASE_URL from the API settings")
    parser.add_argument("--yes", action="store_true", help="actually delete (otherwise a dry run that rolls back)")
    args = parser.parse_args(argv)

    engine = create_database_engine(args.database_url) if args.database_url else app_engine
    with Session(engine) as db:
        before = table_counts(db)
        db.execute(text("CREATE TEMP TABLE doomed_ids (id uuid PRIMARY KEY) ON COMMIT DROP"))
        db.execute(text(DOOMED_IDS))
        s3_keys = list(db.execute(text(
            "DELETE FROM attachments WHERE entity_id IN (SELECT id FROM doomed_ids) RETURNING s3_key"
        )).scalars())
        for statement in DELETES:
            db.execute(text(statement))
        after = table_counts(db)

        print("Rows removed:" if args.yes else "Rows that would be removed (dry run):")
        changed = {name: before[name] - after[name] for name in before if before[name] != after[name]}
        for name in sorted(changed, key=lambda n: -changed[n]):
            print(f"  {name:28} {changed[name]:>8}")
        if not changed:
            print("  nothing")

        if not args.yes:
            db.rollback()
            print("\nNothing was deleted. Run again with --yes to delete.")
            return
        db.commit()

    if s3_keys:
        delete_s3_objects(s3_keys)
    print("Done. Users, accounts and memberships are unchanged.")


if __name__ == "__main__":
    main()
