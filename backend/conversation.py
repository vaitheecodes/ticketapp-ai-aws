"""Pieces shared by ticket threads (backend/main.py) and AI chats
(backend/chats.py)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import BackgroundTasks

from backend import email_utils, graph, handoff_signals
from backend.closing_ack import is_closing_acknowledgment

# Separates a ticket's original text from the AI case summary appended
# on escalation; technicians only see ticket_text in the queue, and
# frontend/src/utils/parseTicketText.js splits on this marker.
ESCALATION_MARKER = "--- Escalated after chat ---"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


Visibility = Literal["public_reply", "internal_note", "system_event"]


def insert_ticket_message(conn, ticket_id: str, sender: str, message: str,
                          author_id: str | None = None, *,
                          visibility: Visibility | None = None,
                          customer_visible: bool = True,
                          subject: str | None = None) -> str:
    """System messages default to customer-visible system events;
    everything else to public replies. Internal notes must be asked for."""
    if visibility is None:
        visibility = "system_event" if sender == "system" else "public_reply"
    message_id = f"MSG-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "INSERT INTO ticket_messages (message_id, ticket_id, sender, message, created_at, "
        "author_id, visibility, customer_visible, subject) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (message_id, ticket_id, sender, message, now(), author_id, visibility,
         int(customer_visible), subject),
    )
    return message_id


def subject_for(ticket) -> str:
    """Tickets from before the subject field: first line of the text."""
    if ticket["subject"]:
        return ticket["subject"]
    first_line = ticket["ticket_text"].split("\n", 1)[0].strip()
    return first_line if len(first_line) <= 80 else first_line[:77].rstrip() + "..."


def customer_row(conn, customer_id: str):
    return conn.execute(
        "SELECT name, email FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()


class Outbox(list):
    """Emails to send after the request's DB work commits (FastAPI
    BackgroundTasks), so SMTP latency or failure never affects the
    response or the stored thread."""

    def flush(self, background_tasks: BackgroundTasks) -> None:
        for to, subject, body in self:
            background_tasks.add_task(email_utils.send_email, to, subject, body)


FollowupGate = Literal["handoff", "close", "continuation", "classify"]


def followup_gate(message_text: str, prior_ai_response: str | None, answered: bool) -> FollowupGate:
    """What a customer follow-up on an AI-handled conversation is, in
    order. `answered`: the AI's last turn was an answer (not a clarifying
    question), so the issue may already be solved.

    1. handoff — accepting a hand-off the AI itself offered ("yes please"
       to "shall I connect you with billing?"). Checked first because with
       an offer on the table "ok"/"sure" means "escalate me", not "done";
       bare gratitude is excluded from the affirmative set.
    2. close — a bare "thanks"/"got it" after an answer isn't a new
       request; re-classifying it would score near-zero confidence and
       wrongly escalate a solved issue.
    3. continuation — a follow-up that refers back to the AI's own last
       answer ("what's the difference?") is answered directly; judged in
       isolation it would collapse classification confidence. Messages
       with escalation language (negation/offensive/coordinated) never
       take this shortcut: "thanks, but it's still not working" is on
       topic, but the hard override must still win.
    4. classify — anything else goes through the full pipeline.

    May raise (the continuation check is an LLM call)."""
    if handoff_signals.is_escalation_acceptance(message_text, prior_ai_response or ""):
        return "handoff"
    if answered and is_closing_acknowledgment(message_text):
        return "close"
    if (
        answered
        and prior_ai_response
        and not graph.has_escalation_language(message_text)
        and graph.is_continuation_of_prior_response(message_text, prior_ai_response)
    ):
        return "continuation"
    return "classify"
