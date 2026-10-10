"""Grant (or revoke) platform admin, which lets a user create accounts and manage users in any account.

The user must have signed in once so their row exists. Uses DATABASE_URL from .env or the environment:
    python scripts/grant_platform_admin.py prjadmin@projecthub.com
    python scripts/grant_platform_admin.py someone@example.com --revoke
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        user = UserRepository(db).get_by_email_normalized(args.email)
        if user is None:
            print(f"No user with email {args.email}; they must sign in once first.", file=sys.stderr)
            return 1
        user.is_platform_admin = not args.revoke
        db.commit()
        print(f"{user.email}: is_platform_admin = {user.is_platform_admin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
