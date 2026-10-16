"""Every ticket status and owner change goes through here, so the history
tables (ticket_status_history, ticket_assignment_history) can't be
skipped by one code path forgetting to write them.

Actors are user ids (CUST-/TECH-/AGENT-/MGR-/ADMIN-) or "system" for the
AI pipeline.
"""

from __future__ import annotations

from backend import app_settings, audit
from backend.conversation import insert_ticket_message, now

SYSTEM = "system"


def status_label(status: str) -> str:
    """The admin-configured display label (app_settings "statuses")."""
    return getattr(app_settings.get("statuses"), status)


def record_created(conn, ticket_id: str, status: str, actor: str) -> None:
    """History row for a brand-new ticket (from_status NULL)."""
    conn.execute(
        "INSERT INTO ticket_status_history (ticket_id, from_status, to_status, changed_by, "
        "changed_at) VALUES (?, NULL, ?, ?, ?)",
        (ticket_id, status, actor, now()),
    )


def set_status(conn, ticket_id: str, new_status: str, actor: str, *,
               event: bool = False, customer_visible: bool = True) -> bool:
    """Change status and log it; no-op (False) if unchanged. With
    `event`, also add a system_event to the thread ("Status changed to
    Resolved"), shown to the customer unless customer_visible=False."""
    row = conn.execute("SELECT status FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if row is None or row["status"] == new_status:
        return False
    conn.execute("UPDATE tickets SET status = ? WHERE ticket_id = ?", (new_status, ticket_id))
    conn.execute(
        "INSERT INTO ticket_status_history (ticket_id, from_status, to_status, changed_by, "
        "changed_at) VALUES (?, ?, ?, ?, ?)",
        (ticket_id, row["status"], new_status, actor, now()),
    )
    audit.record(conn, actor, "ticket.status_changed", "ticket", ticket_id,
                 from_status=row["status"], to_status=new_status)
    if event:
        insert_ticket_message(
            conn, ticket_id, "system", f"Status changed to {status_label(new_status)}.",
            customer_visible=customer_visible,
        )
    return True


def record_assignment(conn, ticket_id: str, from_user: str | None, to_user: str | None,
                      actor: str) -> None:
    conn.execute(
        "INSERT INTO ticket_assignment_history (ticket_id, from_user, to_user, changed_by, "
        "changed_at) VALUES (?, ?, ?, ?, ?)",
        (ticket_id, from_user, to_user, actor, now()),
    )
    audit.record(conn, actor, "ticket.assignment_changed", "ticket", ticket_id,
                 from_user=from_user, to_user=to_user)


def set_assignee(conn, ticket_id: str, new_assignee: str | None, actor: str) -> bool:
    """Change the owner and log it; no-op (False) if unchanged. (Claims
    use their own atomic conditional UPDATE, then record_assignment.)"""
    row = conn.execute(
        "SELECT assigned_technician_id FROM tickets WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()
    if row is None or row["assigned_technician_id"] == new_assignee:
        return False
    conn.execute(
        "UPDATE tickets SET assigned_technician_id = ? WHERE ticket_id = ?",
        (new_assignee, ticket_id),
    )
    record_assignment(conn, ticket_id, row["assigned_technician_id"], new_assignee, actor)
    return True
