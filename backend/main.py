"""FastAPI app wiring auth, ticket, and queue endpoints to the LangGraph
pipeline.

Run from the repo root: uvicorn backend.main:app --reload
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend import auth, db, graph, handoff_signals
from backend.closing_ack import is_closing_acknowledgment
from backend.email_utils import clean_email

app = FastAPI(title="Ticket Resolution Agent")

# Comma-separated list, so the deployed Static Web Apps origin can be set as
# an App Service Application Setting without a code change. Falls back to the
# local Vite dev/preview ports when unset.
#
# Note: these must be explicit origins, not "*" — allow_credentials=True and
# a wildcard origin are mutually exclusive per the CORS spec, and browsers
# reject the combination.
_DEFAULT_ALLOWED_ORIGINS = "http://localhost:5173,http://localhost:4173"
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_graph = None


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    auth.ensure_admin()
    global _graph
    _graph = graph.build_graph()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SignupRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class NewTicketRequest(BaseModel):
    ticket_text: str
    channel: str


class SimulateTicketRequest(BaseModel):
    ticket_text: str
    channel: str = "chat"
    customer_id: str | None = None


class ChatMessageRequest(BaseModel):
    message: str


class ReassignRequest(BaseModel):
    technician_id: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class CreateStaffRequest(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    role: Literal["technician", "manager"]
    temporary_password: str


class ResetPasswordRequest(BaseModel):
    temporary_password: str


class DepartmentRequest(BaseModel):
    name: str = Field(min_length=1)
    categories: list[str] = []


class ManagerDepartmentsRequest(BaseModel):
    department_ids: list[str]


# route -> customer-facing ticket status. "clarify" reads clearly as
# awaiting the customer's reply, distinct from "open" (waiting on a human).
_STATUS_FOR_ROUTE = {"ai": "resolved", "clarify": "awaiting_reply", "human": "open"}

# Shown to the customer, and stored as a system message, when a bare
# closing acknowledgment ("thanks") locks an already-resolved ticket
# instead of silently doing nothing.
_TICKET_CLOSED_MESSAGE = "Ticket marked resolved — conversation closed."

_ESCALATED_MESSAGE = "This conversation has been escalated to a human agent."
_ESCALATION_MARKER = "--- Escalated after chat ---"


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------
def _insert_message(conn, ticket_id: str, sender: str, message: str) -> None:
    conn.execute(
        "INSERT INTO ticket_messages (message_id, ticket_id, sender, message, "
        "created_at) VALUES (?, ?, ?, ?, ?)",
        (
            f"MSG-{uuid.uuid4().hex[:8]}",
            ticket_id,
            sender,
            message,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _build_transcript(messages: list[dict]) -> str:
    speaker = {"customer": "Customer", "ai": "Agent (AI)"}
    return "\n".join(
        f"{speaker.get(m['sender'], m['sender'])}: {m['message']}" for m in messages
    )


def _last_ai_message(conn, ticket_id: str) -> str | None:
    row = conn.execute(
        "SELECT message FROM ticket_messages WHERE ticket_id = ? AND sender = 'ai' "
        "ORDER BY created_at DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    return row["message"] if row else None


def _transcript_for(conn, ticket_id: str) -> str:
    rows = conn.execute(
        "SELECT sender, message FROM ticket_messages WHERE ticket_id = ? "
        "ORDER BY created_at ASC",
        (ticket_id,),
    ).fetchall()
    return _build_transcript([dict(row) for row in rows])


def _escalate_to_human(conn, ticket_id: str, original_text: str, transcript: str,
                       category, confidence) -> None:
    """Shared escalation: summarize the conversation for the human agent
    and fold it into `ticket_text` (agents only ever see ticket_text),
    move the ticket into the open human queue, and tell the customer.
    Used by decide_route's own 'human' route and by both hand-off
    signals in backend/handoff_signals.py — one mechanism, so a ticket
    escalated for any reason reaches the queue looking the same."""
    summary = graph.summarize_case(transcript)
    conn.execute(
        "UPDATE tickets SET route = 'human', status = 'open', "
        "assigned_technician_id = NULL, category = ?, confidence = ?, "
        "ticket_text = ? WHERE ticket_id = ?",
        (
            category,
            confidence,
            f"{original_text}\n\n{_ESCALATION_MARKER}\n{summary}",
            ticket_id,
        ),
    )
    _insert_message(conn, ticket_id, "system", _ESCALATED_MESSAGE)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.post("/api/auth/customer/signup")
def customer_signup(body: SignupRequest):
    """The only public signup. Technicians and managers are created by
    the admin (/api/admin/staff); the admin is bootstrapped from env."""
    return auth.signup_customer(body.name.strip(), body.email.strip(), body.password)


@app.post("/api/auth/login")
def login(body: LoginRequest):
    """One login for every role; the response's `role` tells the
    frontend which portal to open."""
    return auth.login(body.email.strip(), body.password)


@app.get("/api/auth/me")
def me(user: dict = Depends(auth.get_current_user)):
    return {
        "user_id": user["id"],
        "role": user["role"],
        "name": user["name"],
        "email": user["email"],
        "must_change_password": user["must_change_password"],
    }


@app.post("/api/auth/change-password")
def change_password(body: ChangePasswordRequest, user: dict = Depends(auth.get_current_user)):
    auth.change_password(user, body.current_password, body.new_password)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Customer endpoints
# ---------------------------------------------------------------------------
@app.get("/api/tickets/mine")
def tickets_mine(customer_id: str = Depends(auth.get_current_customer)):
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE customer_id = ? ORDER BY created_at DESC",
            (customer_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.post("/api/tickets/new")
def tickets_new(
    body: NewTicketRequest, customer_id: str = Depends(auth.get_current_customer)
):
    ticket_text = body.ticket_text.strip()
    if not ticket_text:
        raise HTTPException(status_code=400, detail="ticket_text cannot be empty")
    if body.channel == "email":
        ticket_text = clean_email(ticket_text)

    ticket_id = f"TCK-{uuid.uuid4().hex[:8]}"
    result = _graph.invoke(
        {
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "channel": body.channel,
            "ticket_text": ticket_text,
        }
    )

    route = result["route"]
    ai_response = result.get("ai_response")

    # An "ai" answer that concedes it can't actually do the thing ("I
    # don't have access to your billing details") hasn't resolved
    # anything, however confidently it was classified — route it to a
    # human instead of recording it as the resolution. Same signal as
    # the mid-chat check in ticket_chat below.
    escalate_for_limitation = route == "ai" and handoff_signals.admits_limitation(
        ai_response or ""
    )
    if escalate_for_limitation:
        route = "human"
    status = _STATUS_FOR_ROUTE[route]

    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO tickets (ticket_id, customer_id, channel, ticket_text, "
            "category, confidence, route, ai_response, status, assigned_technician_id, "
            "clarification_attempts, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_id,
                customer_id,
                body.channel,
                ticket_text,
                result.get("category"),
                result.get("confidence"),
                route,
                ai_response,
                status,
                None,
                result.get("clarification_attempts", 0),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        _insert_message(conn, ticket_id, "customer", ticket_text)
        if ai_response:
            _insert_message(conn, ticket_id, "ai", ai_response)
        if escalate_for_limitation:
            _escalate_to_human(
                conn,
                ticket_id,
                ticket_text,
                _transcript_for(conn, ticket_id),
                result.get("category"),
                result.get("confidence"),
            )
        conn.commit()
    finally:
        conn.close()

    return {"ticket_id": ticket_id, "route": route, "ai_response": ai_response}


@app.get("/api/tickets/{ticket_id}/messages")
def ticket_messages(ticket_id: str, customer_id: str = Depends(auth.get_current_customer)):
    conn = db.get_connection()
    try:
        ticket = conn.execute(
            "SELECT customer_id FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        if ticket["customer_id"] != customer_id:
            raise HTTPException(status_code=403, detail="Not your ticket")

        rows = conn.execute(
            "SELECT sender, message, created_at FROM ticket_messages "
            "WHERE ticket_id = ? ORDER BY created_at ASC",
            (ticket_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.post("/api/tickets/{ticket_id}/chat")
def ticket_chat(
    ticket_id: str,
    body: ChatMessageRequest,
    customer_id: str = Depends(auth.get_current_customer),
):
    message_text = body.message.strip()
    if not message_text:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    conn = db.get_connection()
    try:
        ticket = conn.execute(
            "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        if ticket["customer_id"] != customer_id:
            raise HTTPException(status_code=403, detail="Not your ticket")
        if ticket["route"] == "human":
            raise HTTPException(
                status_code=400,
                detail="This ticket has already been escalated to a human agent.",
            )
        if ticket["status"] == "closed":
            raise HTTPException(status_code=400, detail="This ticket has been closed.")

        # The per-message gate, in order: hand-off acceptance, closing
        # acknowledgment, continuation, then normal classification. Each
        # is a different thing a follow-up message can be, and only the
        # last one needs the confidence machinery meant for triaging
        # brand-new issues.
        prior_ai_response = _last_ai_message(conn, ticket_id)

        # GATE 1 — the customer accepting a hand-off the AI itself
        # offered ("yes please" to "shall I connect you with billing?").
        # That's an escalation request, not a new question: re-running
        # classification on it just produced another round of
        # self-service navigation text instead of a queued ticket.
        # Checked before the closing-ack gate because with an offer on
        # the table "ok"/"sure" means "yes, escalate me", not "we're
        # done" -- bare gratitude ("thanks") is deliberately excluded
        # from the affirmative set, so it still reads as a close.
        if handoff_signals.is_escalation_acceptance(message_text, prior_ai_response or ""):
            _insert_message(conn, ticket_id, "customer", message_text)
            _escalate_to_human(
                conn,
                ticket_id,
                ticket["ticket_text"],
                _transcript_for(conn, ticket_id),
                ticket["category"],
                ticket["confidence"],
            )
            conn.commit()
            return {"route": "human"}

        # GATE 2 — a bare "thanks"/"got it" on an already-resolved
        # ticket isn't a new support request: re-running the graph on it
        # would classify with near-zero confidence and wrongly escalate
        # a solved ticket. Store the message, close the ticket, and skip
        # the pipeline entirely. Ambiguous or substantive follow-ups
        # (a question, "but it's still not working", etc.) fall through
        # to the normal pipeline below, unchanged.
        if ticket["status"] == "resolved" and is_closing_acknowledgment(message_text):
            _insert_message(conn, ticket_id, "customer", message_text)
            _insert_message(conn, ticket_id, "system", _TICKET_CLOSED_MESSAGE)
            conn.execute(
                "UPDATE tickets SET status = 'closed' WHERE ticket_id = ?", (ticket_id,)
            )
            conn.commit()
            return {"route": "closed", "message": _TICKET_CLOSED_MESSAGE}

        # GATE 3 — a follow-up to an already-resolved ticket that refers
        # back to the AI's own last reply ("what's the difference", "can
        # you elaborate") isn't a new support request to triage -- it's a
        # continuation of the same answered thread. classify_ticket
        # judges each message's own classifiability, and a short
        # follow-up fragment has almost no standalone content, which
        # used to collapse decide_route's confidence and wrongly
        # escalate an on-topic question. Skip classify_ticket/
        # decide_route entirely for a genuine continuation and answer
        # directly, grounded in the agent's prior reply. A genuinely new/
        # different message (checked by the same call) falls through to
        # the normal pipeline below, unchanged -- and so does one with an
        # escalation-language signal (negation/offensive/coordinated),
        # checked FIRST and short-circuiting the continuation call
        # entirely: a frustrated "thanks, but it's still not working" is
        # topically a continuation, but the hard override must still win
        # and send it to decide_route's normal escalation path, not get
        # auto-answered just because it's on-topic.
        if (
            ticket["status"] == "resolved"
            and prior_ai_response
            and not graph.has_escalation_language(message_text)
            and graph.is_continuation_of_prior_response(message_text, prior_ai_response)
        ):
            _insert_message(conn, ticket_id, "customer", message_text)
            state = {
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "channel": ticket["channel"],
                "ticket_text": _transcript_for(conn, ticket_id),
                "category": ticket["category"],
                "kb_candidates": graph.retrieve_kb_candidates_for_continuation(
                    prior_ai_response, message_text
                ),
                "continuation": True,
            }
            state.update(graph.fetch_customer_history(state))
            state.update(graph.lookup_order(state))
            ai_response = graph.generate_response(state)["ai_response"]
            _insert_message(conn, ticket_id, "ai", ai_response)

            # Even on the continuation path, an answer that concedes it
            # can't actually help hasn't resolved anything.
            if handoff_signals.admits_limitation(ai_response):
                _escalate_to_human(
                    conn,
                    ticket_id,
                    ticket["ticket_text"],
                    _transcript_for(conn, ticket_id),
                    ticket["category"],
                    ticket["confidence"],
                )
                conn.commit()
                return {"route": "human", "message": ai_response}

            conn.commit()
            return {"route": "ai", "message": ai_response}

        # GATE 4 — normal classification for a genuinely new/different
        # request.
        _insert_message(conn, ticket_id, "customer", message_text)
        transcript = _transcript_for(conn, ticket_id)

        result = _graph.invoke(
            {
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "channel": ticket["channel"],
                "ticket_text": transcript,
                "clarification_attempts": ticket["clarification_attempts"],
            }
        )

        if result["route"] in ("ai", "clarify"):
            _insert_message(conn, ticket_id, "ai", result["ai_response"])

            # An "ai" answer that concedes it can't do the thing is a
            # hand-off signal, not a resolution — escalate instead of
            # recording it as one (see backend/handoff_signals.py).
            if result["route"] == "ai" and handoff_signals.admits_limitation(
                result["ai_response"]
            ):
                _escalate_to_human(
                    conn,
                    ticket_id,
                    ticket["ticket_text"],
                    _transcript_for(conn, ticket_id),
                    result.get("category"),
                    result.get("confidence"),
                )
                conn.commit()
                return {"route": "human", "message": result["ai_response"]}

            conn.execute(
                "UPDATE tickets SET category = ?, confidence = ?, route = ?, "
                "status = ?, clarification_attempts = ? WHERE ticket_id = ?",
                (
                    result.get("category"),
                    result.get("confidence"),
                    result["route"],
                    _STATUS_FOR_ROUTE[result["route"]],
                    result.get("clarification_attempts", 0),
                    ticket_id,
                ),
            )
            conn.commit()
            return {"route": result["route"], "message": result["ai_response"]}

        # decide_route escalated mid-conversation.
        _escalate_to_human(
            conn,
            ticket_id,
            ticket["ticket_text"],
            transcript,
            result.get("category"),
            result.get("confidence"),
        )
        conn.commit()
        return {"route": "human"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Technician endpoints
# ---------------------------------------------------------------------------
@app.get("/api/queue")
def queue(technician_id: str = Depends(auth.get_current_technician)):
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT ticket_id, ticket_text FROM tickets "
            "WHERE status = 'open' AND route = 'human' ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.get("/api/queue/mine")
def queue_mine(technician_id: str = Depends(auth.get_current_technician)):
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT ticket_id, ticket_text, status FROM tickets "
            "WHERE assigned_technician_id = ? ORDER BY created_at ASC",
            (technician_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.post("/api/queue/{ticket_id}/assign")
def queue_assign(ticket_id: str, technician_id: str = Depends(auth.get_current_technician)):
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT status, route FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        if row["status"] != "open" or row["route"] != "human":
            raise HTTPException(status_code=400, detail="Ticket is not available to assign")

        conn.execute(
            "UPDATE tickets SET status = 'assigned', assigned_technician_id = ? "
            "WHERE ticket_id = ?",
            (technician_id, ticket_id),
        )
        conn.commit()
        return {"ticket_id": ticket_id, "status": "assigned"}
    finally:
        conn.close()


@app.post("/api/queue/{ticket_id}/resolve")
def queue_resolve(ticket_id: str, technician_id: str = Depends(auth.get_current_technician)):
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT assigned_technician_id FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        if row["assigned_technician_id"] != technician_id:
            raise HTTPException(status_code=403, detail="Ticket not assigned to you")

        conn.execute(
            "UPDATE tickets SET status = 'resolved' WHERE ticket_id = ?", (ticket_id,)
        )
        conn.commit()
        return {"ticket_id": ticket_id, "status": "resolved"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------
@app.get("/api/admin/tickets")
def admin_tickets(admin_id: str = Depends(auth.get_current_admin)):
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM tickets ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.get("/api/admin/technicians")
def admin_technicians(admin_id: str = Depends(auth.get_current_admin)):
    """Active technicians with live ticket counts — the reassignment
    dropdown on All Tickets."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT s.staff_id, s.name, s.email, "
            "SUM(CASE WHEN t.status = 'assigned' THEN 1 ELSE 0 END) AS active_count, "
            "SUM(CASE WHEN t.status = 'resolved' THEN 1 ELSE 0 END) AS resolved_count "
            "FROM staff s LEFT JOIN tickets t ON t.assigned_technician_id = s.staff_id "
            "WHERE s.role = 'technician' AND s.active = 1 "
            "GROUP BY s.staff_id, s.name, s.email "
            "ORDER BY s.name COLLATE NOCASE"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.post("/api/admin/tickets/{ticket_id}/reassign")
def admin_reassign_ticket(
    ticket_id: str, body: ReassignRequest, admin_id: str = Depends(auth.get_current_admin)
):
    """Admin override: (re)assign a human-routed ticket to a different
    technician, or unassign it back to the open queue (technician_id=null)."""
    conn = db.get_connection()
    try:
        ticket = conn.execute(
            "SELECT route FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        if ticket["route"] != "human":
            raise HTTPException(
                status_code=400,
                detail="Only tickets routed to a human agent can be (re)assigned",
            )

        if body.technician_id:
            technician = conn.execute(
                "SELECT 1 FROM staff WHERE staff_id = ? AND role = 'technician' AND active = 1",
                (body.technician_id,),
            ).fetchone()
            if technician is None:
                raise HTTPException(status_code=404, detail="Technician not found")
            conn.execute(
                "UPDATE tickets SET assigned_technician_id = ?, status = 'assigned' "
                "WHERE ticket_id = ?",
                (body.technician_id, ticket_id),
            )
        else:
            conn.execute(
                "UPDATE tickets SET assigned_technician_id = NULL, status = 'open' "
                "WHERE ticket_id = ?",
                (ticket_id,),
            )
        conn.commit()
        return {"ticket_id": ticket_id, "assigned_technician_id": body.technician_id}
    finally:
        conn.close()


# --- Team (staff accounts) -------------------------------------------------
def _staff_with_departments(conn) -> list[dict]:
    staff = [
        dict(row)
        for row in conn.execute(
            "SELECT staff_id, name, email, role, active, must_change_password FROM staff "
            "ORDER BY role, name COLLATE NOCASE"
        )
    ]
    links: dict[str, list[str]] = {}
    for row in conn.execute("SELECT manager_id, department_id FROM manager_departments"):
        links.setdefault(row["manager_id"], []).append(row["department_id"])
    for person in staff:
        person["active"] = bool(person["active"])
        person["must_change_password"] = bool(person["must_change_password"])
        person["department_ids"] = links.get(person["staff_id"], [])
    return staff


def _get_staff_or_404(conn, staff_id: str):
    row = conn.execute("SELECT * FROM staff WHERE staff_id = ?", (staff_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return row


@app.get("/api/admin/staff")
def admin_list_staff(admin_id: str = Depends(auth.get_current_admin)):
    conn = db.get_connection()
    try:
        return _staff_with_departments(conn)
    finally:
        conn.close()


@app.post("/api/admin/staff")
def admin_create_staff(body: CreateStaffRequest, admin_id: str = Depends(auth.get_current_admin)):
    """Create a technician or manager with a temporary password; they
    must change it on first login (auth.require_role blocks every other
    endpoint until they do)."""
    auth.validate_new_password(body.temporary_password)
    email = body.email.strip()
    conn = db.get_connection()
    try:
        if auth.email_in_use(conn, email):
            raise HTTPException(status_code=400, detail="Email already registered")
        prefix = "MGR" if body.role == "manager" else "TECH"
        staff_id = f"{prefix}-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO staff (staff_id, name, email, password_hash, role, active, "
            "must_change_password) VALUES (?, ?, ?, ?, ?, 1, 1)",
            (staff_id, body.name.strip(), email, auth.hash_password(body.temporary_password), body.role),
        )
        conn.commit()
        (created,) = [p for p in _staff_with_departments(conn) if p["staff_id"] == staff_id]
        return created
    finally:
        conn.close()


def _set_staff_active(staff_id: str, active: bool) -> dict:
    conn = db.get_connection()
    try:
        _get_staff_or_404(conn, staff_id)
        conn.execute("UPDATE staff SET active = ? WHERE staff_id = ?", (int(active), staff_id))
        conn.commit()
        return {"staff_id": staff_id, "active": active}
    finally:
        conn.close()


@app.post("/api/admin/staff/{staff_id}/deactivate")
def admin_deactivate_staff(staff_id: str, admin_id: str = Depends(auth.get_current_admin)):
    """Blocks login and invalidates live sessions immediately. Tickets
    already assigned to them stay put; reassign them from All Tickets."""
    return _set_staff_active(staff_id, False)


@app.post("/api/admin/staff/{staff_id}/activate")
def admin_activate_staff(staff_id: str, admin_id: str = Depends(auth.get_current_admin)):
    return _set_staff_active(staff_id, True)


@app.post("/api/admin/staff/{staff_id}/reset-password")
def admin_reset_staff_password(
    staff_id: str, body: ResetPasswordRequest, admin_id: str = Depends(auth.get_current_admin)
):
    auth.validate_new_password(body.temporary_password)
    conn = db.get_connection()
    try:
        _get_staff_or_404(conn, staff_id)
        conn.execute(
            "UPDATE staff SET password_hash = ?, must_change_password = 1 WHERE staff_id = ?",
            (auth.hash_password(body.temporary_password), staff_id),
        )
        conn.commit()
        return {"staff_id": staff_id, "must_change_password": True}
    finally:
        conn.close()


@app.put("/api/admin/staff/{staff_id}/departments")
def admin_set_manager_departments(
    staff_id: str, body: ManagerDepartmentsRequest, admin_id: str = Depends(auth.get_current_admin)
):
    conn = db.get_connection()
    try:
        if _get_staff_or_404(conn, staff_id)["role"] != "manager":
            raise HTTPException(status_code=400, detail="Only managers own departments")
        department_ids = sorted(set(body.department_ids))
        known = {
            row["department_id"]
            for row in conn.execute("SELECT department_id FROM departments")
        }
        unknown = [d for d in department_ids if d not in known]
        if unknown:
            raise HTTPException(status_code=404, detail=f"Unknown department(s): {', '.join(unknown)}")
        conn.execute("DELETE FROM manager_departments WHERE manager_id = ?", (staff_id,))
        conn.executemany(
            "INSERT INTO manager_departments (manager_id, department_id) VALUES (?, ?)",
            [(staff_id, d) for d in department_ids],
        )
        conn.commit()
        return {"staff_id": staff_id, "department_ids": department_ids}
    finally:
        conn.close()


# --- Departments -----------------------------------------------------------
def _department_rows(conn, where: str = "", params: tuple = ()) -> list[dict]:
    departments = []
    for row in conn.execute(
        f"SELECT department_id, name, categories FROM departments {where} "
        "ORDER BY name COLLATE NOCASE",
        params,
    ):
        dept = dict(row)
        dept["categories"] = json.loads(dept["categories"])
        dept["manager_ids"] = [
            r["manager_id"]
            for r in conn.execute(
                "SELECT manager_id FROM manager_departments WHERE department_id = ?",
                (dept["department_id"],),
            )
        ]
        departments.append(dept)
    return departments


def _validate_department(conn, body: DepartmentRequest, department_id: str | None) -> list[str]:
    """Categories must be real KB categories, and each category may
    belong to only one department — otherwise "route to the department
    that owns this category" has two answers."""
    categories = sorted(set(body.categories))
    invalid = [c for c in categories if c not in graph.CATEGORIES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown category: {', '.join(invalid)}")
    clash = conn.execute(
        "SELECT department_id FROM departments WHERE name = ? COLLATE NOCASE AND department_id != ?",
        (body.name.strip(), department_id or ""),
    ).fetchone()
    if clash:
        raise HTTPException(status_code=400, detail="A department with that name already exists")
    for other in _department_rows(conn):
        if other["department_id"] == department_id:
            continue
        overlap = sorted(set(categories) & set(other["categories"]))
        if overlap:
            raise HTTPException(
                status_code=400,
                detail=f"{', '.join(overlap)} already belongs to {other['name']}",
            )
    return categories


@app.get("/api/admin/departments")
def admin_list_departments(admin_id: str = Depends(auth.get_current_admin)):
    conn = db.get_connection()
    try:
        return {"departments": _department_rows(conn), "categories": graph.CATEGORIES}
    finally:
        conn.close()


@app.post("/api/admin/departments")
def admin_create_department(body: DepartmentRequest, admin_id: str = Depends(auth.get_current_admin)):
    conn = db.get_connection()
    try:
        categories = _validate_department(conn, body, None)
        department_id = f"DEPT-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO departments (department_id, name, categories) VALUES (?, ?, ?)",
            (department_id, body.name.strip(), json.dumps(categories)),
        )
        conn.commit()
        return _department_rows(conn, "WHERE department_id = ?", (department_id,))[0]
    finally:
        conn.close()


@app.put("/api/admin/departments/{department_id}")
def admin_update_department(
    department_id: str, body: DepartmentRequest, admin_id: str = Depends(auth.get_current_admin)
):
    conn = db.get_connection()
    try:
        if not conn.execute(
            "SELECT 1 FROM departments WHERE department_id = ?", (department_id,)
        ).fetchone():
            raise HTTPException(status_code=404, detail="Department not found")
        categories = _validate_department(conn, body, department_id)
        conn.execute(
            "UPDATE departments SET name = ?, categories = ? WHERE department_id = ?",
            (body.name.strip(), json.dumps(categories), department_id),
        )
        conn.commit()
        return _department_rows(conn, "WHERE department_id = ?", (department_id,))[0]
    finally:
        conn.close()


@app.delete("/api/admin/departments/{department_id}")
def admin_delete_department(department_id: str, admin_id: str = Depends(auth.get_current_admin)):
    conn = db.get_connection()
    try:
        deleted = conn.execute(
            "DELETE FROM departments WHERE department_id = ?", (department_id,)
        ).rowcount
        conn.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail="Department not found")
        return {"department_id": department_id, "deleted": True}
    finally:
        conn.close()


@app.post("/api/admin/simulate-ticket")
def admin_simulate_ticket(
    body: SimulateTicketRequest, admin_id: str = Depends(auth.get_current_admin)
):
    """Run a ticket through the pipeline without persisting it — the
    per-ticket classify/route trace view, admin-only. Mirrors what the
    old src/ui/app.py Streamlit demo showed for a single ticket."""
    ticket_text = body.ticket_text.strip()
    if not ticket_text:
        raise HTTPException(status_code=400, detail="ticket_text cannot be empty")
    if body.channel == "email":
        ticket_text = clean_email(ticket_text)

    result = _graph.invoke(
        {
            "ticket_id": f"SIM-{uuid.uuid4().hex[:8]}",
            "customer_id": body.customer_id or f"SIM-CUST-{uuid.uuid4().hex[:6]}",
            "channel": body.channel,
            "ticket_text": ticket_text,
        }
    )
    return result


# ---------------------------------------------------------------------------
# Manager endpoints
# ---------------------------------------------------------------------------
@app.get("/api/manager/departments")
def manager_departments(manager_id: str = Depends(auth.get_current_manager)):
    """The departments (and so the KB categories) this manager owns —
    the scope their dashboard will filter incidents and tickets by."""
    conn = db.get_connection()
    try:
        return _department_rows(
            conn,
            "WHERE department_id IN "
            "(SELECT department_id FROM manager_departments WHERE manager_id = ?)",
            (manager_id,),
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Debug (dev-only, unauthenticated)
# ---------------------------------------------------------------------------
@app.get("/api/debug/routing-policy")
def debug_routing_policy():
    return {
        "confidence_threshold": graph.CONFIDENCE_THRESHOLD,
        "high_risk_confidence_threshold": graph.HIGH_RISK_CONFIDENCE_THRESHOLD,
        "high_risk_categories": sorted(graph.HIGH_RISK_CATEGORIES),
        "escalation_language_tags": sorted(graph.ESCALATION_LANGUAGE_TAGS),
        "categories": graph.CATEGORIES,
        "max_clarification_attempts": graph.MAX_CLARIFICATION_ATTEMPTS,
        "decision_order": [
            "escalation language tags (hard override -> human, always)",
            f"high-risk category ({'/'.join(sorted(graph.HIGH_RISK_CATEGORIES))}) "
            "below its stricter confidence bar -> human immediately, no "
            "clarify round at all",
            "other categories below the base confidence threshold -> "
            "clarify, up to max_clarification_attempts rounds",
            "still below the bar after max attempts -> force human",
            "otherwise -> ai",
        ],
    }
