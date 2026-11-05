"""Pieces shared by ticket threads (backend/main.py) and AI chats
(backend/chats.py)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import BackgroundTasks

from backend import email_utils, graph, handoff_signals, sla
from backend.ai_service import AIError, grounding, suggest_steps, summarize_conversation, summarize_ticket
from backend.ai_service.schemas import HandoffSummary, KBCandidate, SuggestStepsInput
from backend.closing_ack import is_closing_acknowledgment

# Separates a ticket's original text from the AI case summary appended
# on escalation; technicians only see ticket_text in the queue, and
# frontend/src/utils/parseTicketText.js splits on this marker.
ESCALATION_MARKER = "--- Escalated after chat ---"

# The most of a conversation sent to the model: the latest characters,
# starting at a message. Caps the cost of a long chat or ticket thread
# (each message is capped too: 4000 characters).
MAX_TRANSCRIPT_CHARS = 12_000
_MESSAGE_START = re.compile(r"^(Customer|Agent \(AI\)|staff|system|technician|manager|admin):", re.MULTILINE)


def latest_transcript(transcript: str, limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    """The transcript's last `limit` characters, from the first message
    that starts inside them (the whole tail when none does)."""
    if len(transcript) <= limit:
        return transcript
    tail = transcript[-limit:]
    start = _MESSAGE_START.search(tail, 1)
    return tail[start.start():] if start else tail


ESCALATION_REASONS = {
    "customer_request": "The customer asked for a human agent.",
    "customer_raised_ticket": "The customer raised a ticket from the chat.",
    "customer_reopened": "The customer reopened a ticket the AI had resolved.",
    "ai_cannot_resolve": "The AI assistant could not resolve the issue.",
    "kb_no_match": (
        "No knowledge-base article covers this request; the AI told the customer so "
        "instead of improvising steps."
    ),
    "kb_needs_info": (
        "The knowledge base says support needs details from the customer to "
        "investigate; the AI told the customer what to collect and add to this ticket."
    ),
    "routing_policy": (
        "Routing policy required a human (high-risk category, still unclear "
        "after clarifying questions, or escalation language)."
    ),
}


NO_KB_STEPS = "No KB match - needs triage"


def _kb_lines(candidate: KBCandidate, limit: int = 4) -> list[str]:
    """The KB entry's own sentences, as steps that are grounded by definition."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", candidate.response) if s.strip()]
    return sentences[:limit]


def handoff_steps(customer_text: str, summary: HandoffSummary, category: str | None) -> list[str]:
    """Suggested next steps from the KB entries that match what the
    customer wrote (graph.handoff_kb_candidates), never anything else:
    "No KB match - needs triage" without one. Model steps the entry doesn't
    support are dropped; if none are left, the entry's own sentences are
    the steps. [] when the AI is unavailable."""
    candidates = [KBCandidate(**c) for c in graph.handoff_kb_candidates(customer_text)]
    if not candidates:
        return [NO_KB_STEPS]
    try:
        steps = suggest_steps.suggest_steps(
            SuggestStepsInput(summary=summary, category=category, kb_candidates=candidates)
        ).steps
    except AIError:
        return []
    return grounding.grounded_steps(steps, grounding.kb_text(candidates[:1])) or _kb_lines(candidates[0])


def handoff_brief(kind: Literal["chat", "ticket"], conversation: str, reason_key: str,
                  issue: str, category: str | None) -> tuple[HandoffSummary, list[str]]:
    """The technician's brief for an escalation: an AI summary of the
    (public) conversation, tidied in code (summarize_conversation.
    tidy_handoff), plus next steps from matching KB entries only
    (handoff_steps). Each part falls back independently, so a hand-off
    never waits on, or fails because of, the AI provider."""
    reason = ESCALATION_REASONS[reason_key]
    customer_text = graph.customer_authored_text(conversation) or issue
    try:
        if kind == "chat":
            summary = summarize_conversation.summarize_escalation(conversation, reason)
        else:
            summary = summarize_ticket.summarize_ticket(conversation, reason)
    except AIError:
        summary = summarize_conversation.fallback_handoff(issue, reason)
    summary = summarize_conversation.tidy_handoff(summary, customer_text)
    return summary, handoff_steps(customer_text, summary, category)


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
    at = now()
    conn.execute(
        "INSERT INTO ticket_messages (message_id, ticket_id, sender, message, created_at, "
        "author_id, visibility, customer_visible, subject) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (message_id, ticket_id, sender, message, at, author_id, visibility,
         int(customer_visible), subject),
    )
    sla.note_first_response(conn, ticket_id, sender, author_id, visibility, at)
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
        if not email_utils.email_enabled():
            return  # real email is off by default (EMAIL_ENABLED)
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
    if answered and prior_ai_response and not graph.has_escalation_language(message_text):
        try:
            if graph.is_continuation_of_prior_response(message_text, prior_ai_response):
                return "continuation"
        except AIError as exc:
            # The model answered but not in the expected shape: treat the
            # message as new (the full pipeline) rather than failing the
            # turn. Provider errors still raise.
            if exc.failure is None or exc.failure.kind != "parse_schema":
                raise
            exc.failure.outcome = "fallback"
    return "classify"
