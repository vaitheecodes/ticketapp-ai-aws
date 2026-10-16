"""FastAPI app wiring auth, ticket, and queue endpoints to the LangGraph
pipeline.

Run from the repo root: uvicorn backend.main:app --reload
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from backend import auth, db, email_utils, graph, handoff_signals
from backend.closing_ack import is_closing_acknowledgment
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


def _validate_category(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if value not in graph.CATEGORIES:
        raise ValueError("Unknown category")
    return value


class NewTicketRequest(BaseModel):
    """Start a chat. The pre-chat form's one-line issue is ticket_text."""

    ticket_text: str
    channel: str
    subject: str | None = None
    # Customer-chosen category: shown back to them, and kept separately
    # from the classifier's category, which alone drives routing (a
    # customer picking a low-risk category must not bypass the stricter
    # ACCOUNT/DATA confidence bar).
    category: str | None = None
    contact_name: str | None = None

    _check_category = field_validator("category")(_validate_category)


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

_ESCALATED_MESSAGE = "This conversation has been escalated to a human agent."
_ESCALATION_MARKER = "--- Escalated after chat ---"
_REOPENED_MESSAGE = "Ticket reopened by the customer."

# Ticket-type (email-style) hand-off: the customer isn't watching a live
# chat, so tell them in the thread (and by email) what happens next.
_EMAIL_HANDOFF_BODY = (
    "Thanks for the details. This one needs a member of our support team, so "
    "I've passed your request to them along with everything you've sent so "
    "far. A support agent will reply to you by email."
)
_EMAIL_HANDOFF_CLOSING = "You don't need to do anything else for now."

AI_SENDER_NAME = "Site24x7 AI Assistant"
SUPPORT_TEAM_NAME = "Site24x7 Support"


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_message(conn, ticket_id: str, sender: str, message: str,
                    author_id: str | None = None) -> str:
    message_id = f"MSG-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "INSERT INTO ticket_messages (message_id, ticket_id, sender, message, "
        "created_at, author_id) VALUES (?, ?, ?, ?, ?, ?)",
        (message_id, ticket_id, sender, message, _now(), author_id),
    )
    return message_id


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
    rows = conn.execute(
        "SELECT sender, message FROM ticket_messages WHERE ticket_id = ? "
        "ORDER BY created_at ASC, rowid ASC",
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


def _customer_row(conn, customer_id: str):
    return conn.execute(
        "SELECT name, email FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()


def _display_name(conn, ticket) -> str:
    return ticket["contact_name"] or _customer_row(conn, ticket["customer_id"])["name"]


def _subject_for(ticket) -> str:
    if ticket["subject"]:
        return ticket["subject"]
    first_line = ticket["ticket_text"].split("\n", 1)[0].strip()
    return first_line if len(first_line) <= 80 else first_line[:77].rstrip() + "..."


class _Outbox(list):
    """Emails to send after the request's DB work commits (FastAPI
    BackgroundTasks), so SMTP latency or failure never affects the
    response or the stored thread."""

    def flush(self, background_tasks: BackgroundTasks) -> None:
        for to, subject, body in self:
            background_tasks.add_task(email_utils.send_email, to, subject, body)


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
                       outbox: _Outbox) -> None:
    """Shared escalation: summarize the conversation for the technician
    and fold it into `ticket_text` (technicians only ever see
    ticket_text), move the ticket into the open human queue, and tell the
    customer — a system note in a chat, an email-style reply on a
    ticket-type request. Used by decide_route's own 'human' route, both
    hand-off signals in backend/handoff_signals.py, and a customer
    reopening an AI-resolved ticket — one mechanism, so a ticket
    escalated for any reason reaches the queue looking the same."""
    summary = graph.summarize_case(transcript)
    original_text = ticket["ticket_text"].split(f"\n\n{_ESCALATION_MARKER}\n", 1)[0]
    conn.execute(
        "UPDATE tickets SET route = 'human', status = 'open', "
        "assigned_technician_id = NULL, category = ?, confidence = ?, "
        "ticket_text = ? WHERE ticket_id = ?",
        (
            category,
            confidence,
            f"{original_text}\n\n{_ESCALATION_MARKER}\n{summary}",
            ticket["ticket_id"],
        ),
    )
    if ticket["ticket_type"] == "ticket":
        _record_ai_reply(
            conn,
            ticket,
            email_utils.format_email_reply(
                _display_name(conn, ticket), _EMAIL_HANDOFF_BODY, _EMAIL_HANDOFF_CLOSING
            ),
            outbox,
        )
    else:
        _insert_message(conn, ticket["ticket_id"], "system", _ESCALATED_MESSAGE)


def _pipeline_state(conn, ticket, ticket_text: str, **extra) -> dict:
    state = {
        "ticket_id": ticket["ticket_id"],
        "customer_id": ticket["customer_id"],
        "channel": ticket["channel"],
        "ticket_text": ticket_text,
        "reply_format": "email" if ticket["ticket_type"] == "ticket" else "chat",
        "customer_name": _display_name(conn, ticket),
    }
    state.update(extra)
    return state


def _handle_followup(conn, ticket, message_text: str, outbox: _Outbox) -> dict:
    """A customer follow-up on an AI-handled ticket (live chat message or
    email-style reply). The per-message gate, in order: hand-off
    acceptance, closing acknowledgment, continuation, then normal
    classification. Each is a different thing a follow-up message can
    be, and only the last one needs the confidence machinery meant for
    triaging brand-new issues. Stores the customer message itself.
    Caller commits."""
    ticket_id = ticket["ticket_id"]
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
            conn, ticket, _transcript_for(conn, ticket_id),
            ticket["category"], ticket["confidence"], outbox,
        )
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
        conn.execute("UPDATE tickets SET status = 'closed' WHERE ticket_id = ?", (ticket_id,))
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

        # Even on the continuation path, an answer that concedes it
        # can't actually help hasn't resolved anything.
        if handoff_signals.admits_limitation(ai_response):
            _escalate_to_human(
                conn, ticket, _transcript_for(conn, ticket_id),
                ticket["category"], ticket["confidence"], outbox,
            )
            return {"route": "human", "message": ai_response}
        return {"route": "ai", "message": ai_response}

    # GATE 4 — normal classification for a genuinely new/different
    # request.
    _insert_message(conn, ticket_id, "customer", message_text)
    transcript = _transcript_for(conn, ticket_id)
    result = _graph.invoke(
        _pipeline_state(
            conn, ticket, transcript, clarification_attempts=ticket["clarification_attempts"]
        )
    )

    if result["route"] in ("ai", "clarify"):
        _record_ai_reply(conn, ticket, result["ai_response"], outbox)

        # An "ai" answer that concedes it can't do the thing is a
        # hand-off signal, not a resolution — escalate instead of
        # recording it as one (see backend/handoff_signals.py).
        if result["route"] == "ai" and handoff_signals.admits_limitation(result["ai_response"]):
            _escalate_to_human(
                conn, ticket, _transcript_for(conn, ticket_id),
                result.get("category"), result.get("confidence"), outbox,
            )
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
        return {"route": result["route"], "message": result["ai_response"]}

    # decide_route escalated mid-conversation.
    _escalate_to_human(
        conn, ticket, transcript, result.get("category"), result.get("confidence"), outbox
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
    """The customer's ticket list, newest first. A chat is only a ticket
    once it has been handed to the support team (route 'human'); chats the
    AI is handling or has resolved are not listed. Curated fields only:
    no confidence, route reasoning, or the internal case summary."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT t.*, "
            "  (SELECT MAX(m.created_at) FROM ticket_messages m "
            "   WHERE m.ticket_id = t.ticket_id) AS last_message_at, "
            "  EXISTS (SELECT 1 FROM ticket_messages m WHERE m.ticket_id = t.ticket_id "
            "          AND m.sender IN ('ai', 'staff') "
            "          AND m.created_at > COALESCE(t.customer_last_read_at, '')) AS unread "
            "FROM tickets t WHERE t.customer_id = ? "
            "AND (t.ticket_type = 'ticket' OR t.route = 'human') "
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
            for row in conn.execute(
                "SELECT * FROM ticket_messages WHERE ticket_id = ? "
                "ORDER BY created_at ASC, rowid ASC",
                (ticket_id,),
            )
        ]
        conn.execute(
            "UPDATE tickets SET customer_last_read_at = ? WHERE ticket_id = ?", (_now(), ticket_id)
        )
        conn.commit()
        return {
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


@app.post("/api/tickets/new")
def tickets_new(
    body: NewTicketRequest,
    background_tasks: BackgroundTasks,
    customer_id: str = Depends(auth.get_current_customer),
):
    """Start a chat (ticket_type 'chat'): the pre-chat form's one-line
    issue is the first message, answered live by the pipeline."""
    ticket_text = body.ticket_text.strip()
    if not ticket_text:
        raise HTTPException(status_code=400, detail="ticket_text cannot be empty")
    if body.channel == "email":
        ticket_text = clean_email(ticket_text)

    ticket_id = f"TCK-{uuid.uuid4().hex[:8]}"
    conn = db.get_connection()
    try:
        contact_name = (body.contact_name or "").strip() or _customer_row(conn, customer_id)["name"]
    finally:
        conn.close()
    result = _graph.invoke(
        {
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "channel": body.channel,
            "ticket_text": ticket_text,
            "reply_format": "chat",
            "customer_name": contact_name,
        }
    )
    return _store_new_ticket(
        ticket_id, customer_id, ticket_text, result, background_tasks,
        ticket_type="chat", channel=body.channel, subject=(body.subject or "").strip() or None,
        priority="medium", requested_category=body.category, contact_name=contact_name,
    )


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
        result = _graph.invoke(
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
        ticket_type="ticket", channel="email", subject=subject, priority=body.priority,
        requested_category=body.category, contact_name=contact_name,
    )


def _store_new_ticket(ticket_id, customer_id, ticket_text, result, background_tasks, *,
                      ticket_type, channel, subject, priority, requested_category,
                      contact_name) -> dict:
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
                ticket_id, customer_id, channel, ticket_text,
                result.get("category"), result.get("confidence"), route, ai_response,
                _STATUS_FOR_ROUTE[route], None, result.get("clarification_attempts", 0),
                _now(), ticket_type, subject, priority, requested_category, contact_name,
            ),
        )
        ticket = _load_ticket(conn, ticket_id)
        message_id = _insert_message(conn, ticket_id, "customer", ticket_text)
        if ai_response:
            _record_ai_reply(conn, ticket, ai_response, outbox)
        if escalate_for_limitation:
            _escalate_to_human(
                conn, ticket, _transcript_for(conn, ticket_id),
                result.get("category"), result.get("confidence"), outbox,
            )
        elif route == "human" and ticket_type == "ticket" and not result.get("pipeline_failed"):
            # decide_route sent it straight to a human: tell the customer.
            # (Chat escalations show the live chat's own escalation note.)
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
        "ticket_type": ticket_type,
        "route": route,
        "status": _STATUS_FOR_ROUTE[route],
        "ai_response": ai_response,
    }


@app.get("/api/tickets/{ticket_id}/messages")
def ticket_messages(ticket_id: str, customer_id: str = Depends(auth.get_current_customer)):
    conn = db.get_connection()
    try:
        _customer_ticket_or_error(conn, ticket_id, customer_id)
        rows = conn.execute(
            "SELECT sender, message, created_at FROM ticket_messages "
            "WHERE ticket_id = ? ORDER BY created_at ASC, rowid ASC",
            (ticket_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.post("/api/tickets/{ticket_id}/chat")
def ticket_chat(
    ticket_id: str,
    body: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    customer_id: str = Depends(auth.get_current_customer),
):
    message_text = body.message.strip()
    if not message_text:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    outbox = _Outbox()
    conn = db.get_connection()
    try:
        ticket = _customer_ticket_or_error(conn, ticket_id, customer_id)
        if ticket["route"] == "human":
            raise HTTPException(
                status_code=400,
                detail="This ticket has already been escalated to a human agent.",
            )
        if ticket["status"] == "closed":
            raise HTTPException(status_code=400, detail="This ticket has been closed.")
        response = _handle_followup(conn, ticket, message_text, outbox)
        conn.commit()
    finally:
        conn.close()
    outbox.flush(background_tasks)
    return response


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
    the support team it is simply added to the thread for them — and a
    reply to a ticket a technician marked resolved puts it back in their
    hands. Attachments go to the returned message_id."""
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
            if ticket["status"] in ("resolved", "pending_customer"):
                status = "in_progress" if ticket["assigned_technician_id"] else "open"
                conn.execute(
                    "UPDATE tickets SET status = ? WHERE ticket_id = ?", (status, ticket_id)
                )
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
    path (with a case summary). If a technician resolved it, it goes back
    to them (or to the open queue if they've been unassigned)."""
    outbox = _Outbox()
    conn = db.get_connection()
    try:
        ticket = _customer_ticket_or_error(conn, ticket_id, customer_id)
        if ticket["status"] != "resolved":
            raise HTTPException(status_code=400, detail="Only resolved tickets can be reopened")
        _insert_message(conn, ticket_id, "system", _REOPENED_MESSAGE)
        if ticket["route"] == "human":
            status = "in_progress" if ticket["assigned_technician_id"] else "open"
            conn.execute("UPDATE tickets SET status = ? WHERE ticket_id = ?", (status, ticket_id))
        else:
            _escalate_to_human(
                conn, ticket, _transcript_for(conn, ticket_id),
                ticket["category"], ticket["confidence"], outbox,
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
    """The ticket's customer, or any staff/admin, may download."""
    if user["must_change_password"]:
        raise HTTPException(status_code=403, detail="Password change required")
    conn = db.get_connection()
    try:
        if user["role"] == "customer":
            _customer_ticket_or_error(conn, ticket_id, user["id"])
        row = conn.execute(
            "SELECT filename, stored_name, content_type FROM attachments "
            "WHERE attachment_id = ? AND ticket_id = ?",
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
            "UPDATE tickets SET status = 'in_progress', assigned_technician_id = ? "
            "WHERE ticket_id = ?",
            (technician_id, ticket_id),
        )
        conn.commit()
        return {"ticket_id": ticket_id, "status": "in_progress"}
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
            "SUM(CASE WHEN t.status IN ('in_progress', 'pending_customer') THEN 1 ELSE 0 END) "
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
                "UPDATE tickets SET assigned_technician_id = ?, status = 'in_progress' "
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
