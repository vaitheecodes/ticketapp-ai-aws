"""AI support chat: a customer-facing lifecycle separate from tickets.

    active ──(customer confirms solved)──> resolved   + internal review record
       └────(hand-off needed)──────────> escalated  + unassigned ticket

- A chat is not a ticket. It only becomes one on escalation: the customer
  asks for a human, the AI admits it can't do the thing, routing policy
  sends it to a human (high-risk category, too many clarifying rounds,
  escalation language), or the customer clicks "Raise a ticket". The
  ticket (ticket_type 'chat', status open, unassigned) links back to the
  session; the transcript stays in chat_messages, and the AI hand-off
  brief (issue, tried, findings, unresolved questions, suggested next
  steps) goes on the ticket for the technician.
- Resolution needs the customer's confirmation ("thanks" after an answer,
  or the "My issue is solved" button). The chat closes, keeps its
  transcript and an AI summary, and gets a chat_reviews row so staff can
  verify the outcome. No technician ticket is created.
- An AI failure never changes a chat's state: the customer's message is
  kept, everything after it is rolled back, and the customer is told the
  assistant is unavailable (they can still raise a ticket, which never
  depends on the AI).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend import audit, auth, db, graph, handoff_signals, incident_memory, incidents, runtime
from backend import ticket_lifecycle as lifecycle
from backend.ai_service import AIError, summarize_conversation
from backend.conversation import (
    ESCALATION_MARKER,
    customer_row,
    followup_gate,
    handoff_brief,
    insert_ticket_message,
    now,
)

logger = logging.getLogger(__name__)

router = APIRouter()

AI_UNAVAILABLE = (
    "Our AI assistant is unavailable right now. Your messages are saved — "
    "try again in a moment, or raise a ticket and our support team will pick it up."
)
_RESOLVED_REPLY = "Glad that's sorted. I've closed this chat — start a new one any time."

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class StartChatRequest(BaseModel):
    issue: str = Field(min_length=1, max_length=500)
    # Shown back to the customer only; routing uses the classifier's own
    # category so a customer can't pick their way past the high-risk bar.
    category: str | None = None
    contact_name: str | None = Field(default=None, max_length=120)

    @field_validator("category")
    @classmethod
    def _known_category(cls, value):
        if value in (None, ""):
            return None
        if value not in graph.CATEGORIES:
            raise ValueError("Unknown category")
        return value


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class EscalateChatRequest(BaseModel):
    subject: str | None = Field(default=None, max_length=200)


class ReviewUpdateRequest(BaseModel):
    status: Literal["verified", "closed"]


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------
def _session_or_error(conn, session_id: str, customer_id: str):
    session = conn.execute(
        "SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    if session["customer_id"] != customer_id:
        raise HTTPException(status_code=403, detail="Not your chat")
    return session


def _reload(conn, session_id: str):
    return conn.execute("SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,)).fetchone()


def _require_active(session) -> None:
    if session["status"] != "active":
        raise HTTPException(status_code=400, detail=f"This chat is {session['status']}.")


def _insert_chat_message(conn, session_id: str, role: str, content: str) -> None:
    conn.execute(
        "INSERT INTO chat_messages (message_id, session_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"CMSG-{uuid.uuid4().hex[:8]}", session_id, role, content, now()),
    )
    conn.execute("UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?", (now(), session_id))


def transcript_rows(conn, session_id: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT message_id, role, content, created_at FROM chat_messages "
            "WHERE session_id = ? ORDER BY created_at ASC, rowid ASC",
            (session_id,),
        )
    ]


def _transcript_text(conn, session_id: str) -> str:
    speaker = {"customer": "Customer", "ai": "Agent (AI)"}
    return "\n".join(
        f"{speaker[m['role']]}: {m['content']}" for m in transcript_rows(conn, session_id)
    )


def _last_ai(conn, session_id: str) -> str | None:
    row = conn.execute(
        "SELECT content FROM chat_messages WHERE session_id = ? AND role = 'ai' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return row["content"] if row else None


def _after_escalation(conn, session_id: str, background_tasks: BackgroundTasks) -> None:
    """Every caller only acts on an active chat, so an escalated_ticket_id
    now means this request escalated it: look for other customers
    reporting the same thing (after the response)."""
    ticket_id = _reload(conn, session_id)["escalated_ticket_id"]
    if ticket_id:
        background_tasks.add_task(incidents.run_for_ticket, ticket_id)


def _view(conn, session_id: str, ai_error: str | None = None) -> dict:
    """Customer-facing view. The AI summary is internal and not included."""
    session = _reload(conn, session_id)
    return {
        "session_id": session["session_id"],
        "status": session["status"],
        "issue": session["issue"],
        "category": session["requested_category"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "resolved_at": session["resolved_at"],
        "ticket_id": session["escalated_ticket_id"],
        "messages": transcript_rows(conn, session_id),
        "ai_error": ai_error,
    }


def _pipeline_state(session, transcript: str, **extra) -> dict:
    state = {
        "ticket_id": session["session_id"],
        "customer_id": session["customer_id"],
        "channel": "chat",
        "ticket_text": transcript,
        "reply_format": "chat",
        "customer_name": session["contact_name"],
    }
    state.update(extra)
    return state


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------
def _resolve(conn, session) -> None:
    session_id = session["session_id"]
    try:
        summary = summarize_conversation.summarize_resolution(_transcript_text(conn, session_id))
    except AIError:
        summary = summarize_conversation.fallback_resolution(session["issue"], _last_ai(conn, session_id))
    _insert_chat_message(conn, session_id, "ai", _RESOLVED_REPLY)
    conn.execute(
        "UPDATE chat_sessions SET status = 'resolved', resolved_at = ?, summary = ? "
        "WHERE session_id = ?",
        (now(), summary.model_dump_json(), session_id),
    )
    conn.execute(
        "INSERT INTO chat_reviews (review_id, chat_id, summary, status, created_at) "
        "VALUES (?, ?, ?, 'pending_review', ?)",
        (f"REV-{uuid.uuid4().hex[:8]}", session_id, summary.model_dump_json(), now()),
    )


def _escalate(conn, session, reason_key: str, subject: str | None = None) -> str:
    """Create the unassigned ticket for a chat and close the chat as
    escalated. Never depends on the AI: see conversation.handoff_brief."""
    session_id = session["session_id"]
    summary, steps = handoff_brief(
        "chat", _transcript_text(conn, session_id), reason_key, session["issue"], session["category"]
    )

    ticket_id = f"TCK-{uuid.uuid4().hex[:8]}"
    contact_name = session["contact_name"] or customer_row(conn, session["customer_id"])["name"]
    conn.execute(
        "INSERT INTO tickets (ticket_id, customer_id, channel, ticket_text, category, "
        "confidence, route, ai_response, status, assigned_technician_id, "
        "clarification_attempts, created_at, ticket_type, subject, priority, "
        "requested_category, contact_name, chat_session_id, ai_summary, ai_suggestions) "
        "VALUES (?, ?, 'chat', ?, ?, ?, 'human', NULL, 'open', NULL, ?, ?, 'chat', ?, "
        "'medium', ?, ?, ?, ?, ?)",
        (
            ticket_id,
            session["customer_id"],
            f"{session['issue']}\n\n{ESCALATION_MARKER}\n{summary.as_text(steps)}",
            session["category"],
            session["confidence"],
            session["clarification_attempts"],
            now(),
            (subject or "").strip() or session["issue"][:200],
            session["requested_category"],
            contact_name,
            session_id,
            summary.model_dump_json(),
            json.dumps(steps),
        ),
    )
    lifecycle.record_created(
        conn,
        ticket_id,
        "open",
        session["customer_id"] if reason_key.startswith("customer_") else lifecycle.SYSTEM,
    )
    insert_ticket_message(
        conn,
        ticket_id,
        "system",
        f"Created from chat {session_id}. Our support team has the full conversation "
        "and will reply here.",
    )
    _insert_chat_message(
        conn,
        session_id,
        "ai",
        f"I've passed this to our support team as ticket {ticket_id}. They can see "
        "this whole conversation, so you won't need to repeat anything — you'll "
        "find the ticket under My tickets, and they'll reply there.",
    )
    conn.execute(
        "UPDATE chat_sessions SET status = 'escalated', escalated_ticket_id = ?, summary = ? "
        "WHERE session_id = ?",
        (ticket_id, summary.model_dump_json(), session_id),
    )
    return ticket_id


def _run_turn(conn, session, message_text: str) -> str | None:
    """Store the customer's message and let the AI respond. Returns an
    error message for the customer if the AI failed (state unchanged
    apart from the stored message), else None. Caller commits."""
    session_id = session["session_id"]
    prior_ai = _last_ai(conn, session_id)
    _insert_chat_message(conn, session_id, "customer", message_text)
    conn.execute("SAVEPOINT chat_turn")
    try:
        _respond(conn, session, message_text, prior_ai)
        conn.execute("RELEASE chat_turn")
        return None
    except Exception:  # noqa: BLE001 — provider/network/parse failures alike
        logger.exception("AI chat turn failed for %s", session_id)
        conn.execute("ROLLBACK TO chat_turn")
        conn.execute("RELEASE chat_turn")
        return AI_UNAVAILABLE


def _respond(conn, session, message_text: str, prior_ai: str | None) -> None:
    session_id = session["session_id"]
    # "Was there an outage yesterday?" — answered from incident memory only.
    incident_answer = incident_memory.answer_incident_question(conn, session["customer_id"], message_text)
    if incident_answer is not None:
        _insert_chat_message(conn, session_id, "ai", incident_answer)
        conn.execute("UPDATE chat_sessions SET last_route = 'ai' WHERE session_id = ?", (session_id,))
        return

    gate = followup_gate(message_text, prior_ai, answered=session["last_route"] == "ai")

    if gate == "handoff":
        _escalate(conn, session, "customer_request")
        return
    if gate == "close":
        _resolve(conn, session)
        return

    if gate == "continuation":
        state = _pipeline_state(
            session,
            _transcript_text(conn, session_id),
            category=session["category"],
            kb_candidates=graph.retrieve_kb_candidates_for_continuation(prior_ai, message_text),
            continuation=True,
        )
        state.update(graph.fetch_customer_history(state))
        state.update(graph.lookup_order(state))
        result = {"route": "ai", "ai_response": graph.generate_response(state)["ai_response"],
                  "category": session["category"], "confidence": session["confidence"],
                  "clarification_attempts": session["clarification_attempts"]}
    else:
        result = runtime.get_graph().invoke(
            _pipeline_state(
                session,
                _transcript_text(conn, session_id),
                clarification_attempts=session["clarification_attempts"],
            )
        )

    if result["route"] == "human":
        _store_classification(conn, session_id, result, last_route=None)
        _escalate(conn, _reload(conn, session_id), "routing_policy")
        return

    _insert_chat_message(conn, session_id, "ai", result["ai_response"])
    _store_classification(conn, session_id, result, last_route=result["route"])
    # An answer that concedes it can't actually do the thing ("I don't have
    # access to your billing") is a hand-off, not a resolution.
    if result["route"] == "ai" and handoff_signals.admits_limitation(result["ai_response"]):
        _escalate(conn, _reload(conn, session_id), "ai_cannot_resolve")


def _store_classification(conn, session_id: str, result: dict, last_route) -> None:
    conn.execute(
        "UPDATE chat_sessions SET category = ?, confidence = ?, last_route = ?, "
        "clarification_attempts = ? WHERE session_id = ?",
        (
            result.get("category"),
            result.get("confidence"),
            last_route,
            result.get("clarification_attempts", 0),
            session_id,
        ),
    )


# ---------------------------------------------------------------------------
# Customer endpoints
# ---------------------------------------------------------------------------
@router.post("/api/chats")
def start_chat(
    body: StartChatRequest,
    background_tasks: BackgroundTasks,
    customer_id: str = Depends(auth.get_current_customer),
):
    issue = body.issue.strip()
    if not issue:
        raise HTTPException(status_code=400, detail="Describe your issue to start a chat")
    session_id = f"CHAT-{uuid.uuid4().hex[:8]}"
    conn = db.get_connection()
    try:
        contact_name = (body.contact_name or "").strip() or customer_row(conn, customer_id)["name"]
        conn.execute(
            "INSERT INTO chat_sessions (session_id, customer_id, status, issue, created_at, "
            "updated_at, requested_category, contact_name) "
            "VALUES (?, ?, 'active', ?, ?, ?, ?, ?)",
            (session_id, customer_id, issue, now(), now(), body.category, contact_name),
        )
        ai_error = _run_turn(conn, _reload(conn, session_id), issue)
        conn.commit()
        _after_escalation(conn, session_id, background_tasks)
        return _view(conn, session_id, ai_error)
    finally:
        conn.close()


@router.get("/api/chats")
def list_chats(customer_id: str = Depends(auth.get_current_customer)):
    """Chat history, newest first (escalated ones also appear as tickets)."""
    conn = db.get_connection()
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT session_id, status, issue, created_at, updated_at, resolved_at, "
                "escalated_ticket_id AS ticket_id FROM chat_sessions "
                "WHERE customer_id = ? ORDER BY created_at DESC, rowid DESC",
                (customer_id,),
            )
        ]
    finally:
        conn.close()


@router.get("/api/chats/{session_id}")
def get_chat(session_id: str, customer_id: str = Depends(auth.get_current_customer)):
    conn = db.get_connection()
    try:
        _session_or_error(conn, session_id, customer_id)
        return _view(conn, session_id)
    finally:
        conn.close()


@router.post("/api/chats/{session_id}/messages")
def send_chat_message(
    session_id: str,
    body: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    customer_id: str = Depends(auth.get_current_customer),
):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty")
    conn = db.get_connection()
    try:
        session = _session_or_error(conn, session_id, customer_id)
        _require_active(session)
        ai_error = _run_turn(conn, session, message)
        conn.commit()
        _after_escalation(conn, session_id, background_tasks)
        return _view(conn, session_id, ai_error)
    finally:
        conn.close()


@router.post("/api/chats/{session_id}/resolve")
def resolve_chat(session_id: str, customer_id: str = Depends(auth.get_current_customer)):
    """The customer confirms the AI solved it ("My issue is solved")."""
    conn = db.get_connection()
    try:
        session = _session_or_error(conn, session_id, customer_id)
        _require_active(session)
        if _last_ai(conn, session_id) is None:
            raise HTTPException(status_code=400, detail="The assistant hasn't answered yet")
        _resolve(conn, session)
        conn.commit()
        return _view(conn, session_id)
    finally:
        conn.close()


@router.post("/api/chats/{session_id}/escalate")
def escalate_chat(
    session_id: str,
    body: EscalateChatRequest,
    background_tasks: BackgroundTasks,
    customer_id: str = Depends(auth.get_current_customer),
):
    """The customer's "Raise a ticket" button: escalate now. Works even
    while the AI is unavailable."""
    conn = db.get_connection()
    try:
        session = _session_or_error(conn, session_id, customer_id)
        _require_active(session)
        _escalate(conn, session, "customer_raised_ticket", body.subject)
        conn.commit()
        _after_escalation(conn, session_id, background_tasks)
        return _view(conn, session_id)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal review of AI-resolved chats (technicians and admin)
# ---------------------------------------------------------------------------
_get_reviewer = auth.require_role("technician", "admin")

_REVIEW_TRANSITIONS = {"pending_review": {"verified", "closed"}, "verified": {"closed"}}


@router.get("/api/chat-reviews")
def list_chat_reviews(
    status: Literal["pending_review", "verified", "closed"] | None = None,
    reviewer_id: str = Depends(_get_reviewer),
):
    conn = db.get_connection()
    try:
        query = (
            "SELECT r.*, s.issue, s.customer_id, s.resolved_at, c.name AS customer_name "
            "FROM chat_reviews r JOIN chat_sessions s ON s.session_id = r.chat_id "
            "JOIN customers c ON c.customer_id = s.customer_id"
        )
        params: tuple = ()
        if status:
            query += " WHERE r.status = ?"
            params = (status,)
        rows = conn.execute(query + " ORDER BY r.created_at DESC", params).fetchall()
        return [
            {**dict(row), "transcript": transcript_rows(conn, row["chat_id"])}
            for row in rows
        ]
    finally:
        conn.close()


@router.post("/api/chat-reviews/{review_id}")
def update_chat_review(
    review_id: str, body: ReviewUpdateRequest, reviewer_id: str = Depends(_get_reviewer)
):
    conn = db.get_connection()
    try:
        review = conn.execute(
            "SELECT status FROM chat_reviews WHERE review_id = ?", (review_id,)
        ).fetchone()
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        if body.status not in _REVIEW_TRANSITIONS.get(review["status"], set()):
            raise HTTPException(
                status_code=400,
                detail=f"Can't move a {review['status']} review to {body.status}",
            )
        conn.execute(
            "UPDATE chat_reviews SET status = ?, reviewed_by = ?, reviewed_at = ? "
            "WHERE review_id = ?",
            (body.status, reviewer_id, now(), review_id),
        )
        audit.record(conn, reviewer_id, "chat_review.updated", "chat_review", review_id,
                     status=body.status)
        conn.commit()
        return {"review_id": review_id, "status": body.status}
    finally:
        conn.close()
