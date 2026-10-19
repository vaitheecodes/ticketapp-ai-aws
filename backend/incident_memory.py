"""Incident memory: answering "did I have an issue?".

remember() snapshots a confirmed or resolved incident into incident_memory:
category, departments, started/resolved times, the approved customer
summary, the affected customer ids (from linked tickets) and visibility —
plus an embedding of the summary when an embeddings deployment is
configured. It's refreshed when an incident is confirmed, gains tickets,
is resolved and sent, or changes visibility; a dismissed incident is
forgotten. Memories older than INCIDENT_MEMORY_DAYS (after resolution)
are never used.

answer_incident_question() is called on customer chat messages and on
replies to AI-handled tickets:
  1. a cheap keyword gate, so ordinary messages never cost a model call;
  2. ai_service.incident_question turns the message into an absolute time
     window (relative dates resolved in IST) and optional topic;
  3. memories overlapping that window and matching the topic are looked
     up — only ones this customer may see: incidents their own tickets
     are linked to, or public ones;
  4. the reply is built from fixed templates and the stored, staff-
     approved customer summary. The model never writes the answer, so it
     can't invent an incident or a time, and other customers' names,
     tickets and details are never included.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import timedelta

from backend import app_settings
from backend.ai_service import AIError, incident_question, provider
from backend.ai_service.schemas import IncidentQuestionInput
from backend.conversation import now
from backend.timeutil import IST, format_ist, parse, utcnow

logger = logging.getLogger(__name__)

TOPIC_SIMILARITY = 0.75

# Only messages that look like questions about a problem on our side pay
# for the model call.
_GATE = re.compile(
    r"\b(outages?|incidents?|downtime|known (?:issue|problem)|service (?:issue|problem|disruption)"
    r"|(?:were|was|is|are) (?:you|site24x7|it|everything|things|the service|monitoring) (?:down|broken)"
    r"|(?:was|were) there (?:an?|any) (?:issue|problem|outage|incident|disruption)"
    r"|(?:did|has) (?:something|anything) (?:happen|go wrong|break))\b",
    re.IGNORECASE,
)

NO_MATCH = (
    "I couldn't find a known incident matching that time. If you're seeing a problem, "
    "I can raise a ticket so our support team can look into it."
)
NO_MATCH_ON_TICKET = (
    "I couldn't find a known incident matching that time. Our support team can look into "
    "it on this ticket."
)


def memory_days() -> int:
    return app_settings.get("incidents").memory_days


def looks_like_incident_question(message: str) -> bool:
    return bool(_GATE.search(message))


# ---------------------------------------------------------------------------
# Remembering
# ---------------------------------------------------------------------------
def _embed(text: str) -> tuple[str | None, str | None]:
    model = provider.embeddings_deployment()
    if not model or not text:
        return None, None
    try:
        return json.dumps(provider.embed([text])[0]), model
    except AIError:
        return None, None


def remember(conn, incident_id: str) -> None:
    """Upsert the memory of a confirmed/resolved incident; forget anything
    else. Never fails the caller: an embedding failure just leaves the
    memory to match by category and time."""
    cluster = conn.execute(
        "SELECT * FROM incident_clusters WHERE incident_id = ?", (incident_id,)
    ).fetchone()
    if cluster is None or cluster["status"] not in ("confirmed", "resolved"):
        conn.execute("DELETE FROM incident_memory WHERE incident_id = ?", (incident_id,))
        return
    affected = sorted({r["customer_id"] for r in conn.execute(
        "SELECT t.customer_id FROM incident_tickets it JOIN tickets t ON t.ticket_id = it.ticket_id "
        "WHERE it.incident_id = ?", (incident_id,))})
    departments = sorted(r["department_id"] for r in conn.execute(
        "SELECT department_id FROM incident_departments WHERE incident_id = ?", (incident_id,)))
    summary = cluster["customer_summary"] if cluster["summary_status"] == "sent" else None
    previous = conn.execute(
        "SELECT customer_summary, embedding, embedding_model FROM incident_memory WHERE incident_id = ?",
        (incident_id,),
    ).fetchone()
    if previous and previous["customer_summary"] == summary:
        embedding, model = previous["embedding"], previous["embedding_model"]
    else:
        embedding, model = _embed(summary or "")
    conn.execute(
        "INSERT INTO incident_memory (incident_id, status, category, department_ids, started_at, "
        "resolved_at, customer_summary, affected_customer_ids, visibility, embedding, "
        "embedding_model, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(incident_id) DO UPDATE SET status = excluded.status, category = excluded.category, "
        "department_ids = excluded.department_ids, started_at = excluded.started_at, "
        "resolved_at = excluded.resolved_at, customer_summary = excluded.customer_summary, "
        "affected_customer_ids = excluded.affected_customer_ids, visibility = excluded.visibility, "
        "embedding = excluded.embedding, embedding_model = excluded.embedding_model, "
        "updated_at = excluded.updated_at",
        (incident_id, cluster["status"], cluster["category"], json.dumps(departments),
         cluster["started_at"], cluster["resolved_at"] if cluster["status"] == "resolved" else None,
         summary, json.dumps(affected), cluster["visibility"], embedding, model, now()),
    )


# ---------------------------------------------------------------------------
# Looking up
# ---------------------------------------------------------------------------
def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _live_memories(conn) -> list[dict]:
    """Not expired: resolved within memory_days, or still open (confirmed)."""
    cutoff = (utcnow() - timedelta(days=memory_days())).isoformat()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM incident_memory WHERE (status = 'resolved' AND resolved_at >= ?) "
        "OR (status = 'confirmed' AND started_at >= ?)",
        (cutoff, cutoff),
    )]


def _topic_matches(memory: dict, category: str | None, topic: str | None, owner_of: dict) -> bool:
    if category:
        if memory["category"] == category:
            return True
        owner = owner_of.get(category)
        if owner and owner in json.loads(memory["department_ids"]):
            return True
    if topic and memory["embedding"]:
        try:
            vector = provider.embed([topic])[0]
        except AIError:
            vector = None
        if vector and _cosine(vector, json.loads(memory["embedding"])) >= TOPIC_SIMILARITY:
            return True
    return not category and not topic


def find_matches(conn, customer_id: str, window_start, window_end,
                 category: str | None = None, topic: str | None = None) -> list[dict]:
    """Memories this customer may hear about, overlapping the window and
    matching the topic; linked ones first."""
    start, end = parse(window_start), parse(window_end)
    owner_of = {}
    for row in conn.execute("SELECT department_id, categories FROM departments"):
        for c in json.loads(row["categories"]):
            owner_of[c] = row["department_id"]
    matches = []
    for memory in _live_memories(conn):
        incident_start = parse(memory["started_at"]) if memory["started_at"] else None
        incident_end = parse(memory["resolved_at"]) if memory["resolved_at"] else utcnow()
        if incident_start is None or incident_start > end or incident_end < start:
            continue
        linked = customer_id in json.loads(memory["affected_customer_ids"])
        if not linked and memory["visibility"] != "public":
            continue  # never reveal an affected_only incident to anyone else
        if not _topic_matches(memory, category, topic, owner_of):
            continue
        matches.append({**memory, "linked": linked})
    # Linked incidents first, most recent first within each group.
    matches.sort(key=lambda m: m["started_at"] or "", reverse=True)
    matches.sort(key=lambda m: not m["linked"])
    return matches


def _first_report(conn, incident_id: str, customer_id: str):
    return conn.execute(
        "SELECT t.ticket_id, t.created_at FROM incident_tickets it JOIN tickets t "
        "ON t.ticket_id = it.ticket_id WHERE it.incident_id = ? AND t.customer_id = ? "
        "ORDER BY t.created_at LIMIT 1",
        (incident_id, customer_id),
    ).fetchone()


def compose_answer(conn, customer_id: str, matches: list[dict], on_ticket: bool = False) -> str:
    """Fixed templates + the stored customer summary. Only this customer's
    own ticket reference and time are ever mentioned."""
    if not matches:
        return NO_MATCH_ON_TICKET if on_ticket else NO_MATCH
    memory = matches[0]
    resolved = memory["status"] == "resolved" and memory["customer_summary"]
    if memory["linked"]:
        report = _first_report(conn, memory["incident_id"], customer_id)
        reported = (
            f"you reported this on {format_ist(report['created_at'])} (ticket {report['ticket_id']})"
            if report else "this affected your account"
        )
        if resolved:
            return (f"Yes — {reported}. Here's what happened and how it was fixed:\n\n"
                    f"{memory['customer_summary']}")
        return (f"Yes — {reported}. We're aware of this issue and our team is investigating it. "
                "We'll post an update on your ticket when it's resolved.")
    if resolved:
        return f"Yes, there was a known issue around that time:\n\n{memory['customer_summary']}"
    return ("We're aware of an issue affecting some customers around that time, and our team is "
            "investigating it. We'll share an update once it's resolved.")


def answer_incident_question(conn, customer_id: str, message: str, on_ticket: bool = False) -> str | None:
    """The reply to an incident question, or None if the message isn't
    one. Raises AIError if the question can't be interpreted (callers
    treat that as "AI unavailable" and change nothing)."""
    if not looks_like_incident_question(message):
        return None
    question = incident_question.parse_incident_question(IncidentQuestionInput(
        message=message, now_ist=utcnow().astimezone(IST).isoformat(), memory_days=memory_days(),
    ))
    if not question.is_incident_question:
        return None
    matches = find_matches(conn, customer_id, question.window_start, question.window_end,
                           question.category, question.topic)
    return compose_answer(conn, customer_id, matches, on_ticket=on_ticket)


# ---------------------------------------------------------------------------
# Public history
# ---------------------------------------------------------------------------
def public_history(conn) -> list[dict]:
    """visibility=public incidents still in memory: status, times and the
    approved customer summary only — no ids of customers or tickets."""
    rows = [m for m in _live_memories(conn) if m["visibility"] == "public"]
    rows.sort(key=lambda m: m["started_at"] or "", reverse=True)
    return [
        {
            "reference": m["incident_id"],
            "status": m["status"],
            "category": m["category"],
            "started_at": m["started_at"],
            "resolved_at": m["resolved_at"],
            "summary": m["customer_summary"]
            or "We're aware of this issue and are investigating it.",
        }
        for m in rows
    ]
