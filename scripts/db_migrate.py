"""Apply pending PostgreSQL migrations (migrations/postgres/NNNN_*.sql).

    DB_BACKEND=postgres DATABASE_URL=... .venv/bin/python scripts/db_migrate.py
    ... scripts/db_migrate.py --status

The app also runs this at startup (db.init_db), under an advisory lock,
so this script is for applying or checking migrations ahead of a deploy.
Connection settings come from DATABASE_URL or DB_*; they are never
printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db_postgres  # noqa: E402  (also loads .env via backend/__init__)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true", help="list applied and pending migrations")
    args = parser.parse_args()
    print(f"database: {db_postgres.describe()}")
    db_postgres.wait_until_ready()
    if args.status:
        conn = db_postgres.get_connection()
        try:
            exists = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL AS e").fetchone()["e"]
            done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")} if exists else set()
        finally:
            conn.close()
        for path in db_postgres.migration_files():
            print(f"  {'applied' if path.stem in done else 'pending'}  {path.stem}")
        return 0
    applied = db_postgres.migrate()
    print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
