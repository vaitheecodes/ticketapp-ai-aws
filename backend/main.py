"""FastAPI app wiring auth, ticket, and queue endpoints to the LangGraph
pipeline.

Run from the repo root: uvicorn backend.main:app --reload
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from backend import auth, chats, db, email_utils, graph, handoff_signals, runtime, staff_tickets
from backend import ticket_lifecycle as lifecycle
from backend.ai_service import AIError
from backend.conversation import ESCALATION_MARKER as _ESCALATION_MARKER
from backend.conversation import Outbox as _Outbox
from backend.conversation import customer_row as _customer_row
from backend.conversation import followup_gate, handoff_brief
from backend.conversation import insert_ticket_message as _insert_message
from backend.conversation import now as _now
from backend.conversation import subject_for as _subject_for
from backend.email_utils import clean_email

logger = logging.getLogger(__name__)

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

app.include_router(chats.router)
app.include_router(staff_tickets.router)


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    auth.ensure_admin()
    runtime.set_graph(graph.build_graph())


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


def _validate_category(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if value not in graph.CATEGORIES:
        raise ValueError("Unknown category")
    return value


class CreateTicketRequest(BaseModel):
    """Raise a ticket (email-style, no chat)."""

    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    category: str | None = None

    _check_category = field_validator("category")(_validate_category)


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


# route -> ticket status after the pipeline runs. "clarify" means the AI
# asked the customer something, so the ticket waits on them.
_STATUS_FOR_ROUTE = {"ai": "resolved", "clarify": "pending_customer", "human": "open"}

# Shown to the customer, and stored as a system message, when a bare
# closing acknowledgment ("thanks") locks an already-resolved ticket
# instead of silently doing nothing.
_TICKET_CLOSED_MESSAGE = "Ticket marked resolved — conversation closed."

_REOPENED_MESSAGE = "Ticket reopened by the customer."

# Ticket-type (email-style) hand-off: the customer isn't watching a live
# chat, so tell them in the thread (and by email) what happens next.
_EMAIL_HANDOFF_BODY = (
    "Thanks for the details. This one needs a member of our support team, so "
    "I've passed your request to them along with everything you've sent so "
    "far. A support agent will reply to you by email."
)
_EMAIL_HANDOFF_CLOSING = "You don't need to do anything else for now."

_AI_UNAVAILABLE = (
    "Our AI assistant is unavailable right now. Your message is saved and "
    "nothing about your ticket has changed — please try again shortly."
)

AI_SENDER_NAME = "Site24x7 AI Assistant"
SUPPORT_TEAM_NAME = "Site24x7 Support"


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------
def _build_transcript(messages: list[dict]) -> str:
    speaker = {"customer": "Customer", "ai": "Agent (AI)"}
    return "\n".join(
        f"{speaker.get(m['sender'], m['sender'])}: {m['message']}" for m in messages
    )


def _last_ai_message(conn, ticket_id: str) -> str | None:
    row = conn.execute(
        "SELECT message FROM ticket_messages WHERE ticket_id = ? AND sender = 'ai' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    return row["message"] if row else None


def _transcript_for(conn, ticket_id: str) -> str:
    """The public conversation, for AI prompts: internal notes and system
    events never reach the model."""
    rows = conn.execute(
        "SELECT sender, message FROM ticket_messages WHERE ticket_id = ? "
        "AND visibility = 'public_reply' ORDER BY created_at ASC, rowid ASC",
        (ticket_id,),
    ).fetchall()
    return _build_transcript([dict(row) for row in rows])


def _load_ticket(conn, ticket_id: str):
    return conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()


def _customer_ticket_or_error(conn, ticket_id: str, customer_id: str):
    ticket = _load_ticket(conn, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket["customer_id"] != customer_id:
        raise HTTPException(status_code=403, detail="Not your ticket")
    return ticket


def _display_name(conn, ticket) -> str:
    return ticket["contact_name"] or _customer_row(conn, ticket["customer_id"])["name"]


def _record_ai_reply(conn, ticket, text: str, outbox: _Outbox) -> str:
    """Store an AI reply in the thread; for ticket-type requests also
    queue it as a real email (no-op unless SMTP is configured)."""
    message_id = _insert_message(conn, ticket["ticket_id"], "ai", text)
    if ticket["ticket_type"] == "ticket":
        customer = _customer_row(conn, ticket["customer_id"])
        outbox.append(
            (customer["email"], f"Re: [{ticket['ticket_id']}] {_subject_for(ticket)}", text)
        )
    return message_id


def _escalate_to_human(conn, ticket, transcript: str, category, confidence,
                       outbox: _Outbox, reason_key: str) -> None:
    """Hand an AI-handled ticket to the support team: attach the AI
    hand-off brief (summary + suggested steps; staff-only, also appended
    to `ticket_text` so the queue preview shows it), move the ticket into
    the open human queue, and tell the customer with an email-style
    reply. Used by decide_route's own 'human' route, both hand-off
    signals in backend/handoff_signals.py, and a customer reopening an
    AI-resolved ticket — one mechanism, so a ticket escalated for any
    reason reaches the queue looking the same. (Chats escalate through
    backend/chats.py.) The brief never blocks the hand-off: see
    conversation.handoff_brief."""
    summary, steps = handoff_brief(
        "ticket", transcript, reason_key, _subject_for(ticket), category
    )
    original_text = ticket["ticket_text"].split(f"\n\n{_ESCALATION_MARKER}\n", 1)[0]
    conn.execute(
        "UPDATE tickets SET route = 'human', category = ?, confidence = ?, ticket_text = ?, "
        "ai_summary = ?, ai_suggestions = ? WHERE ticket_id = ?",
        (
            category,
            confidence,
            f"{original_text}\n\n{_ESCALATION_MARKER}\n{summary.as_text(steps)}",
            summary.model_dump_json(),
            json.dumps(steps),
            ticket["ticket_id"],
        ),
    )
    lifecycle.set_assignee(conn, ticket["ticket_id"], None, lifecycle.SYSTEM)
    lifecycle.set_status(conn, ticket["ticket_id"], "open", lifecycle.SYSTEM)
    _record_ai_reply(
        conn,
        ticket,
        email_utils.format_email_reply(
            _display_name(conn, ticket), _EMAIL_HANDOFF_BODY, _EMAIL_HANDOFF_CLOSING
        ),
        outbox,
    )


def _pipeline_state(conn, ticket, ticket_text: str, **extra) -> dict:
    state = {
        "ticket_id": ticket["ticket_id"],
        "customer_id": ticket["customer_id"],
        "channel": ticket["channel"],
        "ticket_text": ticket_text,
        "reply_format": "email",
        "customer_name": _display_name(conn, ticket),
    }
    state.update(extra)
    return state


def _handle_followup(conn, ticket, message_text: str, outbox: _Outbox) -> dict:
    """A customer reply on a ticket the AI is still handling (route ai or
    clarify). Stores the message, then acts on conversation.followup_gate.
    If any AI step fails, everything after the stored message is rolled
    back: the ticket is never resolved, closed or escalated because an AI
    call failed. Caller commits."""
    ticket_id = ticket["ticket_id"]
    prior_ai_response = _last_ai_message(conn, ticket_id)
    _insert_message(conn, ticket_id, "customer", message_text)
    conn.execute("SAVEPOINT followup")
    try:
        response = _run_followup(conn, ticket, message_text, prior_ai_response, outbox)
        conn.execute("RELEASE followup")
        return response
    except Exception:  # noqa: BLE001 — provider/network/parse failures alike
        logger.exception("AI follow-up failed for %s", ticket_id)
        conn.execute("ROLLBACK TO followup")
        conn.execute("RELEASE followup")
        outbox.clear()
        return {"route": ticket["route"], "ai_error": _AI_UNAVAILABLE}


def _run_followup(conn, ticket, message_text: str, prior_ai_response, outbox: _Outbox) -> dict:
    ticket_id = ticket["ticket_id"]
    gate = followup_gate(message_text, prior_ai_response, answered=ticket["status"] == "resolved")

    if gate == "handoff":
        _escalate_to_human(
            conn, ticket, _transcript_for(conn, ticket_id),
            ticket["category"], ticket["confidence"], outbox,
            reason_key="customer_request",
        )
        return {"route": "human"}

    if gate == "close":
        _insert_message(conn, ticket_id, "system", _TICKET_CLOSED_MESSAGE)
        lifecycle.set_status(conn, ticket_id, "closed", ticket["customer_id"])
        return {"route": "closed", "message": _TICKET_CLOSED_MESSAGE}

    if gate == "continuation":
        state = _pipeline_state(
            conn,
            ticket,
            _transcript_for(conn, ticket_id),
            category=ticket["category"],
            kb_candidates=graph.retrieve_kb_candidates_for_continuation(
                prior_ai_response, message_text
            ),
            continuation=True,
        )
        state.update(graph.fetch_customer_history(state))
        state.update(graph.lookup_order(state))
        ai_response = graph.generate_response(state)["ai_response"]
        _record_ai_reply(conn, ticket, ai_response, outbox)
        # An answer that concedes it can't actually help hasn't resolved anything.
        if handoff_signals.admits_limitation(ai_response):
            _escalate_to_human(
                conn, ticket, _transcript_for(conn, ticket_id),
                ticket["category"], ticket["confidence"], outbox,
                reason_key="ai_cannot_resolve",
            )
            return {"route": "human", "message": ai_response}
        return {"route": "ai", "message": ai_response}

    transcript = _transcript_for(conn, ticket_id)
    result = runtime.get_graph().invoke(
        _pipeline_state(
            conn, ticket, transcript, clarification_attempts=ticket["clarification_attempts"]
        )
    )
    if result["route"] in ("ai", "clarify"):
        _record_ai_reply(conn, ticket, result["ai_response"], outbox)
        # An "ai" answer that concedes it can't do the thing is a hand-off
        # signal, not a resolution (see backend/handoff_signals.py).
        if result["route"] == "ai" and handoff_signals.admits_limitation(result["ai_response"]):
            _escalate_to_human(
                conn, ticket, _transcript_for(conn, ticket_id),
                result.get("category"), result.get("confidence"), outbox,
                reason_key="ai_cannot_resolve",
            )
            return {"route": "human", "message": result["ai_response"]}
        conn.execute(
            "UPDATE tickets SET category = ?, confidence = ?, route = ?, "
            "clarification_attempts = ? WHERE ticket_id = ?",
            (
                result.get("category"),
                result.get("confidence"),
                result["route"],
                result.get("clarification_attempts", 0),
                ticket_id,
            ),
        )
        lifecycle.set_status(conn, ticket_id, _STATUS_FOR_ROUTE[result["route"]], lifecycle.SYSTEM)
        return {"route": result["route"], "message": result["ai_response"]}

    # decide_route escalated mid-conversation.
    _escalate_to_human(
        conn, ticket, transcript, result.get("category"), result.get("confidence"), outbox,
        reason_key="routing_policy",
    )
    return {"route": "human"}


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
@app.get("/api/categories")
def categories():
    """The 17 KB categories, for the customer's category pickers."""
    return graph.CATEGORIES


@app.get("/api/tickets/mine")
def tickets_mine(customer_id: str = Depends(auth.get_current_customer)):
    """The customer's ticket list, newest first. Chats live in
    /api/chats and only become tickets when escalated (ticket_type
    'chat'). Curated fields only: no confidence, route reasoning, or the
    internal case summary."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT t.*, "
            "  (SELECT MAX(m.created_at) FROM ticket_messages m "
            f"   WHERE m.ticket_id = t.ticket_id AND {db.CUSTOMER_VISIBLE_SQL}) AS last_message_at, "
            "  EXISTS (SELECT 1 FROM ticket_messages m WHERE m.ticket_id = t.ticket_id "
            "          AND m.sender IN ('ai', 'staff') AND m.visibility = 'public_reply' "
            "          AND m.created_at > COALESCE(t.customer_last_read_at, '')) AS unread "
            "FROM tickets t WHERE t.customer_id = ? "
            "ORDER BY t.created_at DESC",
            (customer_id,),
        ).fetchall()
        return [
            {
                "ticket_id": t["ticket_id"],
                "ticket_type": t["ticket_type"],
                "subject": _subject_for(t),
                "status": t["status"],
                "priority": t["priority"],
                "category": t["requested_category"] or t["category"],
                "created_at": t["created_at"],
                "last_updated": t["last_message_at"] or t["created_at"],
                "unread": bool(t["unread"]),
                # Handed to the support team (drives the "Escalated" filter
                # tab; escalation is a route, not one of the five statuses).
                "escalated": t["route"] == "human",
            }
            for t in rows
        ]
    finally:
        conn.close()


def _thread_message(row, customer_name: str, staff_names: dict, attachments: list) -> dict:
    sender = row["sender"]
    if sender == "customer":
        name, role, to = customer_name, "Customer", SUPPORT_TEAM_NAME
    elif sender == "ai":
        name, role, to = AI_SENDER_NAME, "AI Assistant", customer_name
    elif sender == "staff":
        name, role, to = staff_names.get(row["author_id"], "Support Agent"), "Support Agent", customer_name
    else:
        name, role, to = "System", "System", None
    return {
        "message_id": row["message_id"],
        "sender": sender,
        "sender_name": name,
        "sender_role": role,
        "to": to,
        "subject": row["subject"],
        "message": row["message"],
        "created_at": row["created_at"],
        "attachments": attachments,
    }


@app.get("/api/tickets/{ticket_id}")
def ticket_detail(ticket_id: str, customer_id: str = Depends(auth.get_current_customer)):
    """Header + email-style thread for one of the customer's tickets.
    Viewing marks every reply so far as read (clears the unread dot)."""
    conn = db.get_connection()
    try:
        ticket = _customer_ticket_or_error(conn, ticket_id, customer_id)
        customer_name = _display_name(conn, ticket)
        staff_names = {
            r["staff_id"]: r["name"]
            for r in conn.execute(
                "SELECT s.staff_id, s.name FROM staff s JOIN ticket_messages m "
                "ON m.author_id = s.staff_id WHERE m.ticket_id = ?",
                (ticket_id,),
            )
        }
        attachments: dict[str, list] = {}
        for a in conn.execute(
            "SELECT attachment_id, message_id, filename, content_type, size_bytes "
            "FROM attachments WHERE ticket_id = ? ORDER BY created_at ASC, rowid ASC",
            (ticket_id,),
        ):
            attachments.setdefault(a["message_id"], []).append(
                {k: a[k] for k in ("attachment_id", "filename", "content_type", "size_bytes")}
            )
        messages = [
            _thread_message(row, customer_name, staff_names, attachments.get(row["message_id"], []))
            # Internal notes and staff-only events are filtered here, in the
            # query: they never leave the database on a customer request.
            for row in conn.execute(
                "SELECT * FROM ticket_messages WHERE ticket_id = ? "
                f"AND {db.CUSTOMER_VISIBLE_SQL} ORDER BY created_at ASC, rowid ASC",
                (ticket_id,),
            )
        ]
        chat = None
        if ticket["chat_session_id"]:
            chat = {
                "session_id": ticket["chat_session_id"],
                "transcript": chats.transcript_rows(conn, ticket["chat_session_id"]),
            }
        conn.execute(
            "UPDATE tickets SET customer_last_read_at = ? WHERE ticket_id = ?", (_now(), ticket_id)
        )
        conn.commit()
        return {
            "chat": chat,
            "ticket": {
                "ticket_id": ticket["ticket_id"],
                "ticket_type": ticket["ticket_type"],
                "subject": _subject_for(ticket),
                "status": ticket["status"],
                "route": ticket["route"],
                "priority": ticket["priority"],
                "category": ticket["requested_category"] or ticket["category"],
                "created_at": ticket["created_at"],
            },
            "messages": messages,
        }
    finally:
        conn.close()


@app.post("/api/tickets")
def tickets_create(
    body: CreateTicketRequest,
    background_tasks: BackgroundTasks,
    customer_id: str = Depends(auth.get_current_customer),
):
    """Raise a ticket (ticket_type 'ticket'): created immediately, no
    chat. The AI answers in the thread as an email (greeting, KB-grounded
    body, signature), or hands off to the support team with an email
    saying an agent will reply. Attachments are uploaded afterwards to
    the returned message_id. If the pipeline itself fails (LLM outage)
    the ticket is still created and simply waits in the human queue."""
    subject = body.subject.strip()
    description = body.description.strip()
    if not subject or not description:
        raise HTTPException(status_code=400, detail="Subject and description are required")

    ticket_id = f"TCK-{uuid.uuid4().hex[:8]}"
    ticket_text = f"{subject}\n\n{description}"
    conn = db.get_connection()
    try:
        contact_name = _customer_row(conn, customer_id)["name"]
    finally:
        conn.close()
    try:
        result = runtime.get_graph().invoke(
            {
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "channel": "email",
                "ticket_text": ticket_text,
                "reply_format": "email",
                "customer_name": contact_name,
            }
        )
    except Exception:  # noqa: BLE001 — any pipeline failure falls back to a human
        logger.exception("Pipeline failed for new ticket %s; queueing for a human", ticket_id)
        result = {"route": "human", "category": None, "confidence": None, "pipeline_failed": True}

    return _store_new_ticket(
        ticket_id, customer_id, ticket_text, result, background_tasks,
        subject=subject, priority=body.priority, requested_category=body.category,
        contact_name=contact_name,
    )


def _store_new_ticket(ticket_id, customer_id, ticket_text, result, background_tasks, *,
                      subject, priority, requested_category, contact_name) -> dict:
    route = result["route"]
    ai_response = result.get("ai_response")

    # An "ai" answer that concedes it can't actually do the thing ("I
    # don't have access to your billing details") hasn't resolved
    # anything, however confidently it was classified — route it to a
    # human instead of recording it as the resolution. Same signal as
    # the follow-up check in _handle_followup.
    escalate_for_limitation = route == "ai" and handoff_signals.admits_limitation(
        ai_response or ""
    )
    if escalate_for_limitation:
        route = "human"

    outbox = _Outbox()
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO tickets (ticket_id, customer_id, channel, ticket_text, "
            "category, confidence, route, ai_response, status, assigned_technician_id, "
            "clarification_attempts, created_at, ticket_type, subject, priority, "
            "requested_category, contact_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_id, customer_id, "email", ticket_text,
                result.get("category"), result.get("confidence"), route, ai_response,
                _STATUS_FOR_ROUTE[route], None, result.get("clarification_attempts", 0),
                _now(), "ticket", subject, priority, requested_category, contact_name,
            ),
        )
        lifecycle.record_created(conn, ticket_id, _STATUS_FOR_ROUTE[route], customer_id)
        ticket = _load_ticket(conn, ticket_id)
        message_id = _insert_message(conn, ticket_id, "customer", ticket_text)
        if ai_response:
            _record_ai_reply(conn, ticket, ai_response, outbox)
        if escalate_for_limitation:
            _escalate_to_human(
                conn, ticket, _transcript_for(conn, ticket_id),
                result.get("category"), result.get("confidence"), outbox,
                reason_key="ai_cannot_resolve",
            )
        elif route == "human" and not result.get("pipeline_failed"):
            # decide_route sent it straight to a human: tell the customer.
            _record_ai_reply(
                conn,
                ticket,
                email_utils.format_email_reply(
                    contact_name, _EMAIL_HANDOFF_BODY, _EMAIL_HANDOFF_CLOSING
                ),
                outbox,
            )
        conn.commit()
    finally:
        conn.close()
    outbox.flush(background_tasks)

    return {
        "ticket_id": ticket_id,
        "message_id": message_id,
        "ticket_type": "ticket",
        "route": route,
        "status": _STATUS_FOR_ROUTE[route],
        "ai_response": ai_response,
    }


@app.post("/api/tickets/{ticket_id}/reply")
def ticket_reply(
    ticket_id: str,
    body: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    customer_id: str = Depends(auth.get_current_customer),
):
    """Email-style reply from the ticket thread. While the AI is handling
    the ticket it goes through the same follow-up gates as a chat
    message (and the AI answers in the thread). Once the ticket is with
    the support team it is added to the thread for them, and a reply to a
    Pending Customer or Resolved ticket returns it to Open (owner kept).
    Attachments go to the returned message_id."""
    message_text = body.message.strip()
    if not message_text:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    outbox = _Outbox()
    conn = db.get_connection()
    try:
        ticket = _customer_ticket_or_error(conn, ticket_id, customer_id)
        if ticket["status"] == "closed":
            raise HTTPException(status_code=400, detail="This ticket has been closed.")
        if ticket["route"] == "human":
            message_id = _insert_message(conn, ticket_id, "customer", message_text)
            # The customer answered (pending_customer) or isn't satisfied
            # (resolved): either way it's back with the support team.
            if ticket["status"] in ("resolved", "pending_customer"):
                lifecycle.set_status(conn, ticket_id, "open", customer_id)
            response = {"route": "human"}
        else:
            response = _handle_followup(conn, ticket, message_text, outbox)
            message_id = conn.execute(
                "SELECT message_id FROM ticket_messages WHERE ticket_id = ? AND sender = 'customer' "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (ticket_id,),
            ).fetchone()["message_id"]
        conn.commit()
        response["message_id"] = message_id
        response["status"] = _load_ticket(conn, ticket_id)["status"]
    finally:
        conn.close()
    outbox.flush(background_tasks)
    return response


@app.post("/api/tickets/{ticket_id}/reopen")
def ticket_reopen(
    ticket_id: str,
    background_tasks: BackgroundTasks,
    customer_id: str = Depends(auth.get_current_customer),
):
    """Reopen a resolved ticket. If the AI resolved it, the answer didn't
    hold, so it goes to the support team through the normal escalation
    path (with a case summary). If a technician resolved it, it returns to
    Open with its owner kept."""
    outbox = _Outbox()
    conn = db.get_connection()
    try:
        ticket = _customer_ticket_or_error(conn, ticket_id, customer_id)
        if ticket["status"] != "resolved":
            raise HTTPException(status_code=400, detail="Only resolved tickets can be reopened")
        _insert_message(conn, ticket_id, "system", _REOPENED_MESSAGE)
        if ticket["route"] == "human":
            lifecycle.set_status(conn, ticket_id, "open", customer_id)
        else:
            _escalate_to_human(
                conn, ticket, _transcript_for(conn, ticket_id),
                ticket["category"], ticket["confidence"], outbox,
                reason_key="customer_reopened",
            )
        conn.commit()
        status = _load_ticket(conn, ticket_id)["status"]
    finally:
        conn.close()
    outbox.flush(background_tasks)
    return {"ticket_id": ticket_id, "status": status}


# --- Attachments -----------------------------------------------------------
MAX_ATTACHMENTS_PER_MESSAGE = 5
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def _looks_like_text(data: bytes) -> bool:
    return b"\x00" not in data[:8192]


# extension -> (served content type, content check). The content check
# stops e.g. an executable renamed to .png; the served type comes from
# this table, never from the client's Content-Type header.
_ALLOWED_ATTACHMENTS = {
    ".png": ("image/png", lambda d: d.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", lambda d: d.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda d: d.startswith(b"\xff\xd8\xff")),
    ".pdf": ("application/pdf", lambda d: d.startswith(b"%PDF-")),
    ".txt": ("text/plain", _looks_like_text),
    ".log": ("text/plain", _looks_like_text),
}


@app.post("/api/tickets/{ticket_id}/attachments")
def upload_attachments(
    ticket_id: str,
    message_id: str = Form(...),
    files: list[UploadFile] = File(...),
    customer_id: str = Depends(auth.get_current_customer),
):
    """Attach files to one of the customer's own messages on their own
    ticket. All files are validated before any is written: at most 5 per
    message, 10 MB each, png/jpg/pdf/txt/log whose contents match the
    extension. Stored under db.uploads_dir() with a random name."""
    conn = db.get_connection()
    try:
        _customer_ticket_or_error(conn, ticket_id, customer_id)
        message = conn.execute(
            "SELECT sender FROM ticket_messages WHERE message_id = ? AND ticket_id = ?",
            (message_id, ticket_id),
        ).fetchone()
        if message is None:
            raise HTTPException(status_code=404, detail="Message not found on this ticket")
        if message["sender"] != "customer":
            raise HTTPException(status_code=400, detail="Attachments can only be added to your own messages")
        existing = conn.execute(
            "SELECT COUNT(*) FROM attachments WHERE message_id = ?", (message_id,)
        ).fetchone()[0]
        if existing + len(files) > MAX_ATTACHMENTS_PER_MESSAGE:
            raise HTTPException(
                status_code=400,
                detail=f"At most {MAX_ATTACHMENTS_PER_MESSAGE} attachments per message",
            )

        validated = []
        for upload in files:
            filename = Path(upload.filename or "").name
            ext = Path(filename).suffix.lower()
            if ext not in _ALLOWED_ATTACHMENTS:
                raise HTTPException(
                    status_code=400,
                    detail=f"{filename or 'File'}: only PNG, JPG, PDF, TXT and LOG files are allowed",
                )
            data = upload.file.read(MAX_ATTACHMENT_BYTES + 1)
            if len(data) > MAX_ATTACHMENT_BYTES:
                raise HTTPException(status_code=413, detail=f"{filename} is larger than 10 MB")
            if not data:
                raise HTTPException(status_code=400, detail=f"{filename} is empty")
            content_type, matches = _ALLOWED_ATTACHMENTS[ext]
            if not matches(data):
                raise HTTPException(
                    status_code=400, detail=f"{filename} doesn't look like a {ext[1:].upper()} file"
                )
            validated.append((filename, ext, content_type, data))

        directory = db.uploads_dir()
        written: list[Path] = []
        created = []
        try:
            for filename, ext, content_type, data in validated:
                attachment_id = f"ATT-{uuid.uuid4().hex[:12]}"
                stored_name = f"{uuid.uuid4().hex}{ext}"
                path = directory / stored_name
                path.write_bytes(data)
                written.append(path)
                conn.execute(
                    "INSERT INTO attachments (attachment_id, ticket_id, message_id, filename, "
                    "stored_name, content_type, size_bytes, uploaded_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (attachment_id, ticket_id, message_id, filename, stored_name,
                     content_type, len(data), customer_id, _now()),
                )
                created.append(
                    {"attachment_id": attachment_id, "filename": filename,
                     "content_type": content_type, "size_bytes": len(data)}
                )
            conn.commit()
        except Exception:
            conn.rollback()
            for path in written:
                path.unlink(missing_ok=True)
            raise
        return created
    finally:
        conn.close()


@app.get("/api/tickets/{ticket_id}/attachments/{attachment_id}")
def download_attachment(
    ticket_id: str, attachment_id: str, user: dict = Depends(auth.get_current_user)
):
    """The ticket's customer (only from messages they can see), or any
    staff/admin, may download."""
    if user["must_change_password"]:
        raise HTTPException(status_code=403, detail="Password change required")
    conn = db.get_connection()
    try:
        if user["role"] == "customer":
            _customer_ticket_or_error(conn, ticket_id, user["id"])
        visible = f"AND {db.CUSTOMER_VISIBLE_SQL}" if user["role"] == "customer" else ""
        row = conn.execute(
            "SELECT a.filename, a.stored_name, a.content_type FROM attachments a "
            "JOIN ticket_messages m ON m.message_id = a.message_id "
            f"WHERE a.attachment_id = ? AND a.ticket_id = ? {visible}",
            (attachment_id, ticket_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = db.uploads_dir() / row["stored_name"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file is missing")
    return FileResponse(
        path,
        media_type=row["content_type"],
        filename=row["filename"],
        headers={"X-Content-Type-Options": "nosniff"},
    )


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
            "SUM(CASE WHEN t.status NOT IN ('resolved', 'closed') THEN 1 ELSE 0 END) "
            "AS active_count, "
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
        ticket = _load_ticket(conn, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        if ticket["route"] != "human":
            raise HTTPException(
                status_code=400,
                detail="Only tickets routed to a human agent can be (re)assigned",
            )

        if body.technician_id:
            technician = conn.execute(
                "SELECT name FROM staff WHERE staff_id = ? AND role = 'technician' AND active = 1",
                (body.technician_id,),
            ).fetchone()
            if technician is None:
                raise HTTPException(status_code=404, detail="Technician not found")
            note = f"Reassigned to {technician['name']} by the administrator."
        else:
            note = "Returned to the unassigned queue by the administrator."

        if lifecycle.set_assignee(conn, ticket_id, body.technician_id, admin_id):
            _insert_message(conn, ticket_id, "system", note, customer_visible=False)
            if body.technician_id and ticket["status"] == "open":
                lifecycle.set_status(conn, ticket_id, "in_progress", admin_id)
            elif not body.technician_id and ticket["status"] == "in_progress":
                lifecycle.set_status(conn, ticket_id, "open", admin_id)
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

    try:
        result = runtime.get_graph().invoke(
            {
                "ticket_id": f"SIM-{uuid.uuid4().hex[:8]}",
                "customer_id": body.customer_id or f"SIM-CUST-{uuid.uuid4().hex[:6]}",
                "channel": body.channel,
                "ticket_text": ticket_text,
            }
        )
    except AIError as exc:
        raise HTTPException(status_code=503, detail="The AI provider is unavailable right now") from exc
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
