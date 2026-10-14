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

from backend.seed_data import seed_orders

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "ticketapp.db"
DB_PATH = Path(os.environ.get("DB_PATH") or _DEFAULT_DB_PATH)

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admins (
    admin_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id         TEXT PRIMARY KEY,
    customer_id       TEXT NOT NULL REFERENCES customers(customer_id),
    channel           TEXT NOT NULL,
    ticket_text       TEXT NOT NULL,
    category          TEXT,
    confidence        REAL,
    route             TEXT NOT NULL,
    ai_response       TEXT,
    status            TEXT NOT NULL,
    assigned_agent_id TEXT REFERENCES agents(agent_id),
    clarification_attempts INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMP NOT NULL
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
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    # Reads the module attribute rather than the import-time constant so a
    # test (or a script) that repoints DB_PATH still gets its directory
    # created. /home/data on App Service does not exist until something
    # makes it.
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        seed_orders(conn)
    finally:
        conn.close()
