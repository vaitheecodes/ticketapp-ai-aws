"""Ticket thread queries. The customer-visible filter is applied in SQL
(db.CUSTOMER_VISIBLE_SQL), so internal notes and staff-only events never
leave the database on a customer request."""

from __future__ import annotations

from backend import db


def customer_thread(conn, ticket_id: str) -> list:
    return conn.execute(
        "SELECT * FROM ticket_messages WHERE ticket_id = ? "
        f"AND {db.CUSTOMER_VISIBLE_SQL} ORDER BY created_at ASC, rowid ASC",
        (ticket_id,),
    ).fetchall()


def public_replies(conn, ticket_id: str) -> list:
    """The public conversation only: what AI prompts may see."""
    return conn.execute(
        "SELECT sender, message FROM ticket_messages WHERE ticket_id = ? "
        "AND visibility = 'public_reply' ORDER BY created_at ASC, rowid ASC",
        (ticket_id,),
    ).fetchall()
