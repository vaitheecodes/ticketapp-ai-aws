"""Technician workspace and manager oversight.

Technicians work tickets that are with the support team (route 'human'):
  - lists: unassigned queue, my tickets, recently updated, open & pending,
    with search and priority/status filters
  - claim an unassigned ticket (atomic: of two simultaneous claims exactly
    one wins, the other gets 409)
  - as the owner: public replies (shown to the customer) and status
    changes; any technician can add internal notes (never shown to the
    customer)
  - the AI hand-off brief, suggested steps and chat transcript, kept
    apart from the conversation

Managers don't assign. They oversee the tickets in their departments
(categories owned by the departments the admin gave them) and each
technician's workload there, read-only.

Every status/owner change goes through backend/ticket_lifecycle.py, so
history is always written.
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend import audit, auth, chats, db, incidents
from backend import ticket_lifecycle as lifecycle
from backend.conversation import (
    ESCALATION_MARKER,
    Outbox,
    customer_row,
    insert_ticket_message,
    subject_for,
)

router = APIRouter()

TicketStatus = Literal["open", "in_progress", "pending_customer", "resolved", "closed"]

_current_staff = auth.require_user("technician", "manager", "admin")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class StatusRequest(BaseModel):
    status: TicketStatus


class StaffMessageRequest(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    visibility: Literal["public_reply", "internal_note"]
    subject: str | None = Field(default=None, max_length=200)
    # Public replies only: e.g. reply and set Pending Customer in one step.
    set_status: TicketStatus | None = None


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------
def manager_categories(conn, manager_id: str) -> set[str]:
    """Categories owned by the manager's (single) department."""
    row = conn.execute(
        "SELECT d.categories FROM departments d JOIN staff s ON s.department_id = d.department_id "
        "WHERE s.staff_id = ?",
        (manager_id,),
    ).fetchone()
    return set(json.loads(row["categories"])) if row else set()


def _ticket_category(ticket) -> str | None:
    return ticket["category"] or ticket["requested_category"]


def _ticket_for_staff(conn, ticket_id: str, user: dict):
    """The ticket if this staff member may see it: technicians and admin
    see every ticket with the support team; managers only those in their
    departments' categories or linked to an incident they can see.
    AI-handled tickets aren't in the workspace."""
    ticket = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket["route"] != "human" and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="This ticket is being handled by the AI assistant")
    if (
        user["role"] == "manager"
        and _ticket_category(ticket) not in manager_categories(conn, user["id"])
        and not incidents.manager_can_see_ticket(conn, user["id"], ticket_id)
    ):
        raise HTTPException(status_code=403, detail="Ticket is outside your departments")
    return ticket


def _require_owner(ticket, user: dict) -> None:
    if user["role"] != "technician":
        raise HTTPException(status_code=403, detail="Technician role required")
    if ticket["assigned_technician_id"] != user["id"]:
        raise HTTPException(
            status_code=403,
            detail="Claim this ticket first" if ticket["assigned_technician_id"] is None
            else "This ticket belongs to another technician",
        )


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------
_LIST_SELECT = (
    "SELECT t.ticket_id, t.subject, t.ticket_text, t.status, t.priority, t.ticket_type, "
    "COALESCE(t.category, t.requested_category) AS category, t.created_at, "
    "t.assigned_technician_id, c.name AS customer_name, c.email AS customer_email, "
    "s.name AS assignee_name, "
    "COALESCE((SELECT MAX(m.created_at) FROM ticket_messages m "
    "          WHERE m.ticket_id = t.ticket_id), t.created_at) AS last_activity "
    "FROM tickets t JOIN customers c ON c.customer_id = t.customer_id "
    "LEFT JOIN staff s ON s.staff_id = t.assigned_technician_id "
)

_VIEWS = {
    # Oldest first: the longest-waiting ticket is the next to pick up.
    "unassigned": (
        "t.assigned_technician_id IS NULL AND t.status NOT IN ('resolved', 'closed')",
        "t.created_at ASC",
    ),
    "mine": ("t.assigned_technician_id = :me", "last_activity DESC"),
    "recent": ("1 = 1", "last_activity DESC"),
    "open_pending": (
        "t.status IN ('open', 'in_progress', 'pending_customer')",
        "last_activity DESC",
    ),
}


def _list_rows(conn, where: str, order: str, params: dict, *, q=None, priority=None,
               status=None, limit: int = 200) -> list[dict]:
    clauses = ["t.route = 'human'", where]
    if q:
        clauses.append(
            "(t.ticket_id LIKE :q OR t.subject LIKE :q OR t.ticket_text LIKE :q "
            "OR c.name LIKE :q OR c.email LIKE :q)"
        )
        params["q"] = f"%{q.strip()}%"
    if priority:
        clauses.append("t.priority = :priority")
        params["priority"] = priority
    if status:
        clauses.append("t.status = :status")
        params["status"] = status
    rows = conn.execute(
        f"{_LIST_SELECT} WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT {int(limit)}",
        params,
    ).fetchall()
    return [
        {
            **{k: row[k] for k in row.keys() if k != "ticket_text"},
            "subject": subject_for(row),
            # The hand-off brief (if any) is what a technician scans for.
            "preview": row["ticket_text"].split(f"\n\n{ESCALATION_MARKER}\n", 1)[-1][:300],
        }
        for row in rows
    ]


@router.get("/api/staff/tickets")
def staff_ticket_list(
    view: Literal["unassigned", "mine", "recent", "open_pending"] = "unassigned",
    q: str | None = Query(default=None, max_length=200),
    priority: Literal["low", "medium", "high", "urgent"] | None = None,
    status: TicketStatus | None = None,
    technician_id: str = Depends(auth.get_current_technician),
):
    where, order = _VIEWS[view]
    conn = db.get_connection()
    try:
        return _list_rows(conn, where, order, {"me": technician_id}, q=q, priority=priority, status=status)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Ticket workspace
# ---------------------------------------------------------------------------
def _names(conn) -> dict[str, str]:
    names = {lifecycle.SYSTEM: "AI assistant"}
    for table, id_col in (("staff", "staff_id"), ("admins", "admin_id"), ("customers", "customer_id")):
        for row in conn.execute(f"SELECT {id_col} AS id, name FROM {table}"):
            names[row["id"]] = row["name"]
    return names


def _parse_json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _workspace(conn, ticket_id: str, user: dict) -> dict:
    ticket = _ticket_for_staff(conn, ticket_id, user)
    names = _names(conn)
    customer = customer_row(conn, ticket["customer_id"])
    owner = ticket["assigned_technician_id"]
    is_tech = user["role"] == "technician"
    open_for_work = ticket["status"] not in ("resolved", "closed")

    attachments: dict[str, list] = {}
    for a in conn.execute(
        "SELECT attachment_id, message_id, filename, content_type, size_bytes FROM attachments "
        "WHERE ticket_id = ? ORDER BY created_at, rowid",
        (ticket_id,),
    ):
        attachments.setdefault(a["message_id"], []).append(
            {k: a[k] for k in ("attachment_id", "filename", "content_type", "size_bytes")}
        )

    messages = []
    for m in conn.execute(
        "SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY created_at, rowid", (ticket_id,)
    ):
        if m["sender"] == "customer":
            author = ticket["contact_name"] or customer["name"]
        elif m["sender"] == "ai":
            author = "Site24x7 AI Assistant"
        elif m["sender"] == "staff":
            author = names.get(m["author_id"], "Support Agent")
        else:
            author = "System"
        messages.append({
            "message_id": m["message_id"],
            "sender": m["sender"],
            "author": author,
            "visibility": m["visibility"],
            "customer_visible": bool(m["customer_visible"]),
            "subject": m["subject"],
            "message": m["message"],
            "created_at": m["created_at"],
            "attachments": attachments.get(m["message_id"], []),
        })

    parts = ticket["ticket_text"].split(f"\n\n{ESCALATION_MARKER}\n", 1)
    return {
        "ticket": {
            "ticket_id": ticket["ticket_id"],
            "subject": subject_for(ticket),
            "description": parts[0],
            "status": ticket["status"],
            "priority": ticket["priority"],
            "ticket_type": ticket["ticket_type"],
            "category": _ticket_category(ticket),
            "created_at": ticket["created_at"],
            "customer": {"name": ticket["contact_name"] or customer["name"], "email": customer["email"]},
            "assignee": {"id": owner, "name": names.get(owner)} if owner else None,
        },
        "messages": messages,
        # Kept apart from the conversation: the technician's AI panel.
        "ai": {
            "summary": _parse_json(ticket["ai_summary"], None),
            "suggestions": _parse_json(ticket["ai_suggestions"], []),
            # Hand-offs from the email-style flow carry a free-text brief.
            "case_summary": parts[1] if len(parts) == 2 else None,
        },
        "chat": (
            {"session_id": ticket["chat_session_id"],
             "transcript": chats.transcript_rows(conn, ticket["chat_session_id"])}
            if ticket["chat_session_id"] else None
        ),
        # Incidents this ticket was linked to by correlation.
        "incidents": [
            dict(r) for r in conn.execute(
                "SELECT c.incident_id, c.title, c.status FROM incident_tickets it "
                "JOIN incident_clusters c ON c.incident_id = it.incident_id WHERE it.ticket_id = ? "
                "ORDER BY c.created_at", (ticket_id,))
        ],
        "status_history": [
            {**dict(r), "changed_by_name": names.get(r["changed_by"], r["changed_by"])}
            for r in conn.execute(
                "SELECT from_status, to_status, changed_by, changed_at FROM ticket_status_history "
                "WHERE ticket_id = ? ORDER BY id", (ticket_id,))
        ],
        "assignment_history": [
            {**dict(r),
             "from_name": names.get(r["from_user"]), "to_name": names.get(r["to_user"]),
             "changed_by_name": names.get(r["changed_by"], r["changed_by"])}
            for r in conn.execute(
                "SELECT from_user, to_user, changed_by, changed_at FROM ticket_assignment_history "
                "WHERE ticket_id = ? ORDER BY id", (ticket_id,))
        ],
        "can": {
            "claim": is_tech and owner is None and open_for_work,
            "reply": is_tech and owner == user["id"],
            "change_status": is_tech and owner == user["id"],
            "note": is_tech,
        },
    }


@router.get("/api/staff/tickets/{ticket_id}")
def staff_ticket_detail(ticket_id: str, user: dict = Depends(_current_staff)):
    conn = db.get_connection()
    try:
        return _workspace(conn, ticket_id, user)
    finally:
        conn.close()


@router.post("/api/staff/tickets/{ticket_id}/claim")
def claim_ticket(ticket_id: str, technician_id: str = Depends(auth.get_current_technician)):
    """Take an unassigned ticket. BEGIN IMMEDIATE serialises writers and
    the UPDATE only matches while the ticket is still unassigned, so of
    two simultaneous claims exactly one succeeds; the other gets 409."""
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        claimed = conn.execute(
            "UPDATE tickets SET assigned_technician_id = ? WHERE ticket_id = ? "
            "AND assigned_technician_id IS NULL AND route = 'human' "
            "AND status NOT IN ('resolved', 'closed')",
            (technician_id, ticket_id),
        ).rowcount
        if not claimed:
            ticket = conn.execute(
                "SELECT route, assigned_technician_id FROM tickets WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            conn.rollback()
            if ticket is None:
                raise HTTPException(status_code=404, detail="Ticket not found")
            if ticket["assigned_technician_id"]:
                raise HTTPException(status_code=409, detail="Someone else already claimed this ticket")
            raise HTTPException(status_code=409, detail="This ticket can't be claimed")
        lifecycle.record_assignment(conn, ticket_id, None, technician_id, technician_id)
        audit.record(conn, technician_id, "ticket.claimed", "ticket", ticket_id)
        lifecycle.set_status(conn, ticket_id, "in_progress", technician_id)
        name = conn.execute("SELECT name FROM staff WHERE staff_id = ?", (technician_id,)).fetchone()["name"]
        insert_ticket_message(conn, ticket_id, "system", f"{name} is now handling your ticket.")
        conn.commit()
        return _workspace(conn, ticket_id, {"id": technician_id, "role": "technician"})
    finally:
        conn.close()


@router.post("/api/staff/tickets/{ticket_id}/status")
def change_status(ticket_id: str, body: StatusRequest, user: dict = Depends(_current_staff)):
    conn = db.get_connection()
    try:
        ticket = _ticket_for_staff(conn, ticket_id, user)
        _require_owner(ticket, user)
        lifecycle.set_status(conn, ticket_id, body.status, user["id"], event=True)
        conn.commit()
        return _workspace(conn, ticket_id, user)
    finally:
        conn.close()


@router.post("/api/staff/tickets/{ticket_id}/messages")
def post_staff_message(
    ticket_id: str,
    body: StaffMessageRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(_current_staff),
):
    """Public reply (owner only; added to the customer's thread and
    emailed when email is configured) or internal note (any technician;
    staff only)."""
    if user["role"] != "technician":
        raise HTTPException(status_code=403, detail="Technician role required")
    outbox = Outbox()
    conn = db.get_connection()
    try:
        ticket = _ticket_for_staff(conn, ticket_id, user)
        text = body.body.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message cannot be empty")
        if body.visibility == "internal_note":
            if body.set_status:
                raise HTTPException(status_code=400, detail="Change status separately from a note")
            insert_ticket_message(
                conn, ticket_id, "staff", text, user["id"],
                visibility="internal_note", customer_visible=False,
            )
        else:
            _require_owner(ticket, user)
            if ticket["status"] == "closed":
                raise HTTPException(status_code=400, detail="Reopen the ticket before replying")
            subject = (body.subject or "").strip() or f"Re: [{ticket_id}] {subject_for(ticket)}"
            insert_ticket_message(conn, ticket_id, "staff", text, user["id"], subject=subject)
            outbox.append((customer_row(conn, ticket["customer_id"])["email"], subject, text))
            if body.set_status:
                lifecycle.set_status(conn, ticket_id, body.set_status, user["id"], event=True)
        conn.commit()
        result = _workspace(conn, ticket_id, user)
    finally:
        conn.close()
    outbox.flush(background_tasks)
    return result


# ---------------------------------------------------------------------------
# Manager oversight
# ---------------------------------------------------------------------------
@router.get("/api/manager/overview")
def manager_overview(manager_id: str = Depends(auth.get_current_manager)):
    """Tickets in the manager's departments and each technician's
    workload on them. Read-only: managers oversee, technicians claim."""
    conn = db.get_connection()
    try:
        categories = sorted(manager_categories(conn, manager_id))
        if not categories:
            return {"categories": [], "tickets": [], "workload": [], "unassigned": 0}
        marks = ", ".join(f":c{i}" for i in range(len(categories)))
        params = {f"c{i}": c for i, c in enumerate(categories)}
        in_scope = f"COALESCE(t.category, t.requested_category) IN ({marks})"
        tickets = _list_rows(conn, in_scope, "last_activity DESC", dict(params))

        workload = [
            dict(row)
            for row in conn.execute(
                "SELECT s.staff_id, s.name, "
                "COALESCE(SUM(CASE WHEN t.status = 'open' THEN 1 ELSE 0 END), 0) AS open, "
                "COALESCE(SUM(CASE WHEN t.status = 'in_progress' THEN 1 ELSE 0 END), 0) AS in_progress, "
                "COALESCE(SUM(CASE WHEN t.status = 'pending_customer' THEN 1 ELSE 0 END), 0) AS pending_customer, "
                "COALESCE(SUM(CASE WHEN t.status = 'resolved' THEN 1 ELSE 0 END), 0) AS resolved "
                "FROM staff s LEFT JOIN tickets t ON t.assigned_technician_id = s.staff_id "
                f"AND t.route = 'human' AND {in_scope} "
                "WHERE s.role = 'technician' AND s.active = 1 "
                "GROUP BY s.staff_id, s.name ORDER BY s.name COLLATE NOCASE",
                params,
            )
        ]
        unassigned = sum(
            1 for t in tickets
            if t["assigned_technician_id"] is None and t["status"] not in ("resolved", "closed")
        )
        return {"categories": categories, "tickets": tickets, "workload": workload,
                "unassigned": unassigned}
    finally:
        conn.close()
