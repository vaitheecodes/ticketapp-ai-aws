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

import json
import os
import sqlite3
import uuid
from pathlib import Path

from backend.seed_data import seed_demo_staff, seed_departments, seed_orders

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "ticketapp.db"
DB_PATH = Path(os.environ.get("DB_PATH") or _DEFAULT_DB_PATH)

# Ticket statuses (customer- and staff-facing, one vocabulary):
#   open              waiting on the support team (human queue, unassigned)
#   in_progress       a technician has picked it up
#   pending_customer  waiting on the customer (e.g. the AI asked a question)
#   resolved          answered, by the AI or a technician
#   closed            done; no further replies accepted
TICKET_STATUSES = ("open", "in_progress", "pending_customer", "resolved", "closed")

# The only ticket_messages a customer endpoint may ever return.
CUSTOMER_VISIBLE_SQL = (
    "(visibility = 'public_reply' OR (visibility = 'system_event' AND customer_visible = 1))"
)

# Pre-Stage-2 status names -> current ones (migrated in place on startup).
_LEGACY_STATUSES = {"awaiting_reply": "pending_customer", "assigned": "in_progress"}

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
    created_at             TIMESTAMP NOT NULL,
    ticket_type            TEXT NOT NULL DEFAULT 'chat',
    subject                TEXT,
    priority               TEXT NOT NULL DEFAULT 'medium',
    requested_category     TEXT,
    contact_name           TEXT,
    customer_last_read_at  TIMESTAMP,
    -- Set when the ticket was created by escalating a chat. The chat's
    -- transcript stays in chat_messages (not copied into the thread).
    chat_session_id        TEXT REFERENCES chat_sessions(session_id),
    -- Staff-only AI output (JSON): never returned by customer endpoints.
    ai_summary             TEXT,
    ai_suggestions         TEXT
);

-- sender: customer | ai | staff | system. author_id is the staff_id for
-- staff messages (null otherwise).
-- visibility: public_reply (the conversation), internal_note (staff only,
-- never returned by a customer endpoint), system_event (status/assignment
-- changes; shown to the customer only when customer_visible = 1).
-- Customer queries filter on these columns in SQL: CUSTOMER_VISIBLE_SQL.
CREATE TABLE IF NOT EXISTS ticket_messages (
    message_id       TEXT PRIMARY KEY,
    ticket_id        TEXT NOT NULL REFERENCES tickets(ticket_id),
    sender           TEXT NOT NULL,
    message          TEXT NOT NULL,
    created_at       TIMESTAMP NOT NULL,
    author_id        TEXT,
    visibility       TEXT NOT NULL DEFAULT 'public_reply',
    customer_visible INTEGER NOT NULL DEFAULT 1,
    subject          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages(ticket_id, created_at);

-- Every status / owner change, by whom ('system' for the AI pipeline).
CREATE TABLE IF NOT EXISTS ticket_status_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   TEXT NOT NULL REFERENCES tickets(ticket_id),
    from_status TEXT,
    to_status   TEXT NOT NULL,
    changed_by  TEXT NOT NULL,
    changed_at  TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_status_history_ticket ON ticket_status_history(ticket_id);

CREATE TABLE IF NOT EXISTS ticket_assignment_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   TEXT NOT NULL REFERENCES tickets(ticket_id),
    from_user   TEXT,
    to_user     TEXT,
    changed_by  TEXT NOT NULL,
    changed_at  TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assignment_history_ticket ON ticket_assignment_history(ticket_id);

-- Customer-facing AI chat, a lifecycle separate from tickets:
--   active     the AI is handling it
--   resolved   the customer confirmed it's solved (summary + review record)
--   escalated  handed to the support team; escalated_ticket_id is the ticket
--   abandoned  ended without resolution or escalation
-- summary is the AI's JSON summary (resolution or escalation); internal only.
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id             TEXT PRIMARY KEY,
    customer_id            TEXT NOT NULL REFERENCES customers(customer_id),
    status                 TEXT NOT NULL DEFAULT 'active',
    issue                  TEXT NOT NULL,
    summary                TEXT,
    resolved_at            TIMESTAMP,
    created_at             TIMESTAMP NOT NULL,
    updated_at             TIMESTAMP NOT NULL,
    requested_category     TEXT,
    contact_name           TEXT,
    category               TEXT,
    confidence             REAL,
    last_route             TEXT,
    clarification_attempts INTEGER NOT NULL DEFAULT 0,
    escalated_ticket_id    TEXT,
    legacy_ticket_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_customer ON chat_sessions(customer_id, created_at);

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id  TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(session_id),
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, created_at);

-- Internal record for every AI-resolved chat, so staff can verify the
-- outcome and close it separately from the (already closed) customer chat.
CREATE TABLE IF NOT EXISTS chat_reviews (
    review_id   TEXT PRIMARY KEY,
    chat_id     TEXT NOT NULL UNIQUE REFERENCES chat_sessions(session_id),
    summary     TEXT,
    status      TEXT NOT NULL DEFAULT 'pending_review',
    reviewed_by TEXT,
    reviewed_at TIMESTAMP,
    created_at  TIMESTAMP NOT NULL
);

-- Files live on disk under uploads_dir() with a server-generated
-- stored_name; `filename` is the customer's original name, display only.
CREATE TABLE IF NOT EXISTS attachments (
    attachment_id TEXT PRIMARY KEY,
    ticket_id     TEXT NOT NULL REFERENCES tickets(ticket_id),
    message_id    TEXT NOT NULL REFERENCES ticket_messages(message_id),
    filename      TEXT NOT NULL,
    stored_name   TEXT NOT NULL UNIQUE,
    content_type  TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    uploaded_by   TEXT NOT NULL,
    created_at    TIMESTAMP NOT NULL
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


# Columns added after the first release. CREATE TABLE IF NOT EXISTS
# never alters an existing table, so older databases get them here.
_ADDED_COLUMNS = {
    "tickets": [
        ("ticket_type", "TEXT NOT NULL DEFAULT 'chat'"),
        ("subject", "TEXT"),
        ("priority", "TEXT NOT NULL DEFAULT 'medium'"),
        ("requested_category", "TEXT"),
        ("contact_name", "TEXT"),
        ("customer_last_read_at", "TIMESTAMP"),
        ("chat_session_id", "TEXT REFERENCES chat_sessions(session_id)"),
        ("ai_summary", "TEXT"),
        ("ai_suggestions", "TEXT"),
    ],
    "ticket_messages": [
        ("author_id", "TEXT"),
        ("visibility", "TEXT NOT NULL DEFAULT 'public_reply'"),
        ("customer_visible", "INTEGER NOT NULL DEFAULT 1"),
        ("subject", "TEXT"),
    ],
}


def _add_missing_columns(conn) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = _columns(conn, table)
        for name, ddl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    for old, new in _LEGACY_STATUSES.items():
        conn.execute("UPDATE tickets SET status = ? WHERE status = ?", (new, old))
    # Messages from before visibility types: system notes were always shown
    # to the customer, so they become customer-visible system events.
    conn.execute(
        "UPDATE ticket_messages SET visibility = 'system_event' "
        "WHERE sender = 'system' AND visibility = 'public_reply'"
    )


_LEGACY_ESCALATION_PREFIX = "This conversation has been escalated"
_LEGACY_REVIEW_SUMMARY = json.dumps(
    {"problem": None, "troubleshooting": [], "solution": None,
     "note": "Migrated from a pre-chat-session conversation; no AI summary was generated."}
)


def _migrate_chat_tickets(conn) -> None:
    """Before chat sessions existed, every chat was a `tickets` row with
    ticket_type='chat'. Move them to chat_sessions/chat_messages
    (idempotent: only rows with no chat_session_id are touched).

    - Escalated chats (route 'human') stay tickets. The pre-escalation
      conversation moves into the linked session's chat_messages; later
      thread messages (and the escalation note) stay in the ticket.
    - Every other chat was never a ticket in the new model: its messages
      move into a session ('resolved' if the customer closed it, else
      'active' so they can pick it up again) and the ticket row is
      removed. A resolved one gets a pending review record. Rows with
      attachments are left untouched rather than risk losing a file link.
    Data is moved, never dropped."""
    legacy = conn.execute(
        "SELECT * FROM tickets WHERE ticket_type = 'chat' AND chat_session_id IS NULL"
    ).fetchall()
    for ticket in legacy:
        ticket_id = ticket["ticket_id"]
        if conn.execute(
            "SELECT 1 FROM attachments WHERE ticket_id = ?", (ticket_id,)
        ).fetchone():
            continue
        messages = conn.execute(
            "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY created_at, rowid",
            (ticket_id,),
        ).fetchall()
        escalated = ticket["route"] == "human"
        if escalated:
            cut = next(
                (i for i, m in enumerate(messages)
                 if m["sender"] == "system" and m["message"].startswith(_LEGACY_ESCALATION_PREFIX)),
                len(messages),
            )
            moved = [m for m in messages[:cut] if m["sender"] in ("customer", "ai")]
            status = "escalated"
        else:
            moved = [m for m in messages if m["sender"] in ("customer", "ai")]
            status = "resolved" if ticket["status"] == "closed" else "active"

        session_id = f"CHAT-{uuid.uuid4().hex[:8]}"
        issue = ticket["subject"] or ticket["ticket_text"].split("\n", 1)[0][:200]
        last_at = moved[-1]["created_at"] if moved else ticket["created_at"]
        conn.execute(
            "INSERT INTO chat_sessions (session_id, customer_id, status, issue, resolved_at, "
            "created_at, updated_at, requested_category, contact_name, category, confidence, "
            "last_route, clarification_attempts, escalated_ticket_id, legacy_ticket_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, ticket["customer_id"], status, issue,
                last_at if status == "resolved" else None,
                ticket["created_at"], last_at, ticket["requested_category"],
                ticket["contact_name"], ticket["category"], ticket["confidence"],
                None if escalated else ticket["route"], ticket["clarification_attempts"],
                ticket_id if escalated else None, ticket_id,
            ),
        )
        conn.executemany(
            "INSERT INTO chat_messages (message_id, session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(m["message_id"], session_id, m["sender"], m["message"], m["created_at"]) for m in moved],
        )
        if status == "resolved":
            conn.execute(
                "INSERT INTO chat_reviews (review_id, chat_id, summary, status, created_at) "
                "VALUES (?, ?, ?, 'pending_review', ?)",
                (f"REV-{uuid.uuid4().hex[:8]}", session_id, _LEGACY_REVIEW_SUMMARY, last_at),
            )
        if escalated:
            conn.executemany(
                "DELETE FROM ticket_messages WHERE message_id = ?",
                [(m["message_id"],) for m in moved],
            )
            conn.execute(
                "UPDATE tickets SET chat_session_id = ? WHERE ticket_id = ?", (session_id, ticket_id)
            )
        else:
            conn.execute("DELETE FROM ticket_messages WHERE ticket_id = ?", (ticket_id,))
            conn.execute("DELETE FROM tickets WHERE ticket_id = ?", (ticket_id,))


def uploads_dir() -> Path:
    """Attachment storage, next to the database so it follows DB_PATH: on
    App Service DB_PATH=/home/data/ticketapp.db puts uploads under
    /home/data/uploads, which persists across restarts."""
    path = Path(DB_PATH).parent / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


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
        _add_missing_columns(conn)
        conn.commit()
        with conn:
            _migrate_chat_tickets(conn)
        seed_orders(conn)
        seed_departments(conn)
        seed_demo_staff(conn)
    finally:
        conn.close()
