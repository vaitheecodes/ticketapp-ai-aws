"""SQLite connection + schema init for the ticket resolution app.

Phase 1: a single local SQLite file. Phase 2 swaps this module for an
Azure SQL/Cosmos client — nothing outside this file should know how a
connection is obtained.

`DB_PATH` is overridable by env var because on Azure App Service only
/home persists across restarts and deploys — the rest of the container
filesystem is ephemeral, so the default path next to this module would
silently lose every ticket on a restart. In production set
DB_PATH=/home/data/ticketapp.db.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from backend.seed_data import seed_demo_staff, seed_departments, seed_orders

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "ticketapp.db"
DB_PATH = Path(os.environ.get("DB_PATH") or _DEFAULT_DB_PATH)

# Staff = technicians and managers, one table with a role column. The
# single admin and customers stay in their own tables: customers own
# tickets (FK), and the admin is bootstrapped from env, never created
# through the API.
SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staff (
    staff_id             TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    email                TEXT NOT NULL UNIQUE,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'technician',
    active               INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admins (
    admin_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id              TEXT PRIMARY KEY,
    customer_id            TEXT NOT NULL REFERENCES customers(customer_id),
    channel                TEXT NOT NULL,
    ticket_text            TEXT NOT NULL,
    category               TEXT,
    confidence             REAL,
    route                  TEXT NOT NULL,
    ai_response            TEXT,
    status                 TEXT NOT NULL,
    assigned_technician_id TEXT REFERENCES staff(staff_id),
    clarification_attempts INTEGER NOT NULL DEFAULT 0,
    created_at             TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_messages (
    message_id  TEXT PRIMARY KEY,
    ticket_id   TEXT NOT NULL REFERENCES tickets(ticket_id),
    sender      TEXT NOT NULL,
    message     TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id       TEXT PRIMARY KEY,
    customer_email TEXT NOT NULL,
    status         TEXT NOT NULL,
    items          TEXT NOT NULL,
    order_date     TEXT NOT NULL
);

-- categories: JSON array of KB category names (graph.CATEGORIES). A
-- category belongs to at most one department (enforced in main.py) so
-- routing a ticket to "the department that owns its category" is never
-- ambiguous.
CREATE TABLE IF NOT EXISTS departments (
    department_id TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    categories    TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS manager_departments (
    manager_id    TEXT NOT NULL REFERENCES staff(staff_id) ON DELETE CASCADE,
    department_id TEXT NOT NULL REFERENCES departments(department_id) ON DELETE CASCADE,
    PRIMARY KEY (manager_id, department_id)
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_agents_to_staff(conn) -> None:
    """Pre-roles databases have an `agents` table and
    `tickets.assigned_agent_id`. Rename both in place: SQLite (>= 3.26,
    legacy_alter_table off) rewrites the FK in `tickets` to follow the
    renamed table/column, so existing assignments survive. Every legacy
    agent becomes a technician via the `role` column default."""
    if _table_exists(conn, "agents") and not _table_exists(conn, "staff"):
        conn.execute("ALTER TABLE agents RENAME TO staff")
        conn.execute("ALTER TABLE staff RENAME COLUMN agent_id TO staff_id")
    if _table_exists(conn, "staff"):
        cols = _columns(conn, "staff")
        if "role" not in cols:
            conn.execute("ALTER TABLE staff ADD COLUMN role TEXT NOT NULL DEFAULT 'technician'")
        if "active" not in cols:
            conn.execute("ALTER TABLE staff ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        if "must_change_password" not in cols:
            conn.execute(
                "ALTER TABLE staff ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
            )
    if _table_exists(conn, "tickets") and "assigned_agent_id" in _columns(conn, "tickets"):
        conn.execute(
            "ALTER TABLE tickets RENAME COLUMN assigned_agent_id TO assigned_technician_id"
        )


def init_db() -> None:
    # Reads the module attribute rather than the import-time constant so a
    # test (or a script) that repoints DB_PATH still gets its directory
    # created. /home/data on App Service does not exist until something
    # makes it.
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    try:
        # Migrate before SCHEMA, or CREATE TABLE IF NOT EXISTS would make
        # an empty `staff` next to the legacy `agents` table.
        _migrate_agents_to_staff(conn)
        conn.executescript(SCHEMA)
        conn.commit()
        seed_orders(conn)
        seed_departments(conn)
        seed_demo_staff(conn)
    finally:
        conn.close()
