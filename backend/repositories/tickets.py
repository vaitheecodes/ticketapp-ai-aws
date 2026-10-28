"""Ticket writes that must be atomic under concurrency."""

from __future__ import annotations

from backend import db

_CLAIM = (
    "UPDATE tickets SET assigned_technician_id = ? WHERE ticket_id = ? "
    "AND assigned_technician_id IS NULL AND route = 'human' "
    "AND status NOT IN ('resolved', 'closed')"
)


def claim(conn, ticket_id: str, technician_id: str) -> bool:
    """Assign an unassigned, human-routed, open ticket to `technician_id`.
    Of any number of concurrent claims exactly one returns True.

    Postgres: one UPDATE ... RETURNING. A concurrent claim waits on the
    row lock; when the winner commits, Postgres re-checks the loser's
    WHERE against the new row (assigned_technician_id is set) and updates
    nothing. SQLite: BEGIN IMMEDIATE takes the write lock first, so the
    claims run one after another and only the first matches."""
    if db.is_postgres(conn):
        return conn.execute(_CLAIM + " RETURNING ticket_id", (technician_id, ticket_id)).fetchone() is not None
    db.begin_write(conn, f"ticket-claim:{ticket_id}")
    return conn.execute(_CLAIM, (technician_id, ticket_id)).rowcount == 1
