"""LangGraph pipeline: fetch_customer_history -> retrieve_kb_candidates ->
classify_ticket -> decide_route -> {lookup_order -> generate_response |
ask_clarifying_question | human_queue}.

Routing policy (thresholds, high-risk categories, escalation language
tags) is ported verbatim from src/agent/state.py + src/agent/nodes/
decide_route.py — same policy, wired to a live LLM call and real ticket
persistence instead of a keyword-stub classifier.

retrieve_kb_candidates (backend/kb_retrieval.py) scores the ticket text
against the 386 canonical Site24x7 KB entries (src/pipeline's silver
tables) before classification even runs, so classify_ticket,
ask_clarifying_question, and generate_response are all grounded in the
same retrieved evidence instead of each guessing independently — a
strong direct KB match raises classify_ticket's confidence past a
conservative raw LLM score, and a genuine multi-candidate ambiguity
gets named specifically in ask_clarifying_question's prompt instead of
a free-floating question about categories that don't exist.

A chat follow-up to an already-resolved ticket does NOT always re-enter
this graph at classify_ticket: backend/main.py's ticket_chat first
checks is_closing_acknowledgment (a bare "thanks" closes the ticket,
see backend/closing_ack.py) and then is_continuation_of_prior_response
(does the new message refer back to the agent's own last reply, e.g.
"what's the difference" after an explanation?) — a short follow-up
fragment has almost no standalone content for classify_ticket to judge
confidently, which used to collapse decide_route's confidence and
escalate an on-topic question the conversation already answers. A
continuation calls lookup_order + generate_response directly (with
retrieve_kb_candidates_for_continuation grounding on the agent's prior
reply plus the new message), skipping classify_ticket/decide_route for
that turn entirely. Only a genuinely new/different message falls
through to the normal classify_ticket -> decide_route path below.

Model calls live in backend/ai_service (one module per operation, with
validated pydantic input/output); the nodes here build those inputs and
apply the routing policy. A provider failure raises AIError out of the
graph, and every caller treats that as "the AI is unavailable" without
changing ticket or chat state.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph
from typing_extensions import NotRequired

from backend import db, kb_retrieval
from backend.ai_service import categorize_ticket as categorize_op
from backend.ai_service import chat_response
from backend.ai_service.schemas import CATEGORIES as _CATEGORIES
from backend.ai_service.schemas import (
    AnswerInput,
    CategorizeInput,
    ClarifyInput,
    ContinuationInput,
    HistoryItem,
    KBCandidate,
)

# ---------------------------------------------------------------------------
# Routing config — ported from src/agent/state.py
# ---------------------------------------------------------------------------
CONFIDENCE_THRESHOLD = 0.75
HIGH_RISK_CONFIDENCE_THRESHOLD = 0.90
HIGH_RISK_CATEGORIES = {"ACCOUNT", "DATA"}
ESCALATION_LANGUAGE_TAGS = {"offensive_language", "coordinated_syntax", "negation"}

# Ambiguous tickets get this many clarifying rounds before decide_route
# forces a human escalation instead of asking yet another question.
MAX_CLARIFICATION_ATTEMPTS = 3

# 17-category enum, ported from src/pipeline/config.py:CATEGORY_INTENTS
CATEGORIES = list(_CATEGORIES)

HISTORY_LOOKBACK = 5

# None of the 17 Site24x7 categories are order/e-commerce related — the
# `orders` mock table (backend/seed_data.py) and lookup_order below exist
# only for the literal case where the ticket text names an explicit
# ORD-XXXXX order number (kept as a defensive, harmless no-op for this
# product rather than ripped out). This must stay empty: a category-based
# trigger like the old Bitext-era {"ORDER","REFUND","DELIVERY","SHIPPING",
# "CANCEL"} would wrongly ask every MONITORING/AGENT/ALERTS/... ticket to
# verify an order — that leak is exactly what the Site24x7 swap surfaced.
_ORDER_RELATED_CATEGORIES: set[str] = set()
_ORDER_ID_RE = re.compile(r"\bORD-\d+\b", re.IGNORECASE)

# classify_ticket's raw LLM-reported confidence is often too conservative
# for short, common phrasings ("help on the website monitor") against
# this taxonomy the model has never seen examples of. When KB retrieval
# (backend/kb_retrieval.py) finds a strong direct match, that grounded
# evidence is used to raise confidence instead of trusting the bare LLM
# number — mapped linearly from kb_retrieval.STRONG_MATCH_SCORE (treated
# as "just clears the base confidence bar") up to a near-1.0 ceiling for
# a near-exact match.
_KB_CONFIDENCE_FLOOR = 0.78
_KB_CONFIDENCE_CEILING = 0.97
_KB_CONFIDENCE_SLOPE = 0.5


def _kb_grounded_confidence(score: float) -> float:
    return min(
        _KB_CONFIDENCE_CEILING,
        _KB_CONFIDENCE_FLOOR + (score - kb_retrieval.STRONG_MATCH_SCORE) * _KB_CONFIDENCE_SLOPE,
    )

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
class TicketState(TypedDict):
    ticket_id: str
    customer_id: str
    channel: str
    ticket_text: str

    customer_history: NotRequired[list[dict]]

    kb_candidates: NotRequired[list[dict]]

    category: NotRequired[str]
    confidence: NotRequired[float]
    language_tags: NotRequired[list[str]]
    clarification_attempts: NotRequired[int]
    order_lookup: NotRequired[dict | None]

    # True when this generate_response call is answering a follow-up
    # continuation of an already-resolved ticket (see
    # is_continuation_of_prior_response) rather than a fresh classify_
    # ticket/decide_route "ai" resolution -- tweaks the prompt to treat
    # the transcript as an ongoing exchange it should build on.
    continuation: NotRequired[bool]

    # "email" for ticket-type requests (raised via the form, answered in
    # an email-style thread): the reply is wrapped in a fixed greeting/
    # closing/signature addressed to customer_name (ai_service.chat_response).
    # Absent/"chat" keeps the conversational chat reply.
    reply_format: NotRequired[Literal["chat", "email"]]
    customer_name: NotRequired[str]

    route: NotRequired[Literal["ai", "human", "clarify"]]
    route_reason: NotRequired[str]

    ai_response: NotRequired[str]


# ---------------------------------------------------------------------------
# language-tag heuristic — stands in for the dataset's pre-decoded W/C/N
# flags, since real incoming tickets have no such annotation
# ---------------------------------------------------------------------------
_PROFANITY_WORDS = {
    "damn", "hell", "crap", "shit", "fuck", "bullshit", "garbage", "useless",
    "terrible", "awful", "pathetic", "ridiculous", "unacceptable",
}
_NEGATION_WORDS = {"not", "no", "never", "n't", "won't", "can't", "didn't", "isn't"}
_COORDINATING_CONJUNCTIONS = {"and", "but", "also", "then", "so"}


def _detect_language_tags(text: str) -> list[str]:
    words = re.findall(r"[a-z']+", text.lower())
    word_set = set(words)
    tags = []

    if word_set & _PROFANITY_WORDS:
        tags.append("offensive_language")
    if word_set & _NEGATION_WORDS:
        tags.append("negation")
    # crude "compound sentence" signal: multiple coordinating conjunctions
    # strung across a longer message
    if len(words) > 15 and len(word_set & _COORDINATING_CONJUNCTIONS) >= 2:
        tags.append("coordinated_syntax")

    return tags


def has_escalation_language(text: str) -> bool:
    """True if `text` trips one of decide_route's hard-override language
    tags (offensive language, negation, coordinated/compound phrasing).
    Used by ticket_chat to make sure the continuation shortcut (see
    is_continuation_of_prior_response) never bypasses this override — a
    continuation check judges topical relevance, not safety/urgency, and
    the escalation override must still win regardless of how clearly
    on-topic a frustrated or abusive follow-up is (e.g. "thanks, but
    it's still not working" is topically a continuation, but the
    negation means it still needs a human, not an auto-answer)."""
    return bool(set(_detect_language_tags(text)) & ESCALATION_LANGUAGE_TAGS)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def fetch_customer_history(state: TicketState) -> dict:
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT ticket_text, category, status FROM tickets "
            "WHERE customer_id = ? ORDER BY created_at DESC LIMIT ?",
            (state["customer_id"], HISTORY_LOOKBACK),
        ).fetchall()
        history = [dict(row) for row in rows]
    finally:
        conn.close()
    return {"customer_history": history}


# A multi-turn ticket_text is a transcript built by main.py's
# _build_transcript ("Customer: ...\nAgent (AI): ...\n..."). Only the
# customer's own words are useful as a KB search query — the AI's own
# (possibly already off-track) questions are noise that measurably
# drags retrieval toward the wrong candidates once concatenated in.
_CUSTOMER_LINE_RE = re.compile(r"^Customer:\s*(.*)$", re.MULTILINE)


def _customer_authored_text(ticket_text: str) -> str:
    lines = _CUSTOMER_LINE_RE.findall(ticket_text)
    return "\n".join(lines) if lines else ticket_text


def _kb_candidates_to_dicts(candidates: list[kb_retrieval.KBCandidate]) -> list[dict]:
    return [
        {
            "kb_entry_id": c.entry.kb_entry_id,
            "category": c.entry.category,
            "intent": c.entry.intent,
            "instruction": c.entry.instruction,
            "response": c.entry.response,
            "score": round(c.score, 3),
        }
        for c in candidates
    ]


def retrieve_kb_candidates(state: TicketState) -> dict:
    """Runs before classify_ticket so classification, clarification, and
    response generation can all be grounded in the same retrieved
    evidence instead of each guessing independently. See
    backend/kb_retrieval.py."""
    query = _customer_authored_text(state["ticket_text"])
    candidates = kb_retrieval.retrieve_candidates(query, top_k=3)
    return {"kb_candidates": _kb_candidates_to_dicts(candidates)}


def kb_candidates_for(text: str, top_k: int = 3) -> list[dict]:
    """KB retrieval for arbitrary text (e.g. grounding a technician's
    suggested next steps in the hand-off brief)."""
    return _kb_candidates_to_dicts(kb_retrieval.retrieve_candidates(text, top_k=top_k))


def retrieve_kb_candidates_for_continuation(
    prior_ai_response: str, message_text: str, top_k: int = 3
) -> list[dict]:
    """KB retrieval for a follow-up/continuation turn (see
    is_continuation_of_prior_response below): searches on BOTH the
    agent's own prior reply and the customer's new message, not just
    the new message alone. A short follow-up fragment ("what is the
    difference") has almost no standalone topic vocabulary, while the
    prior reply carries the actual substance (e.g. the monitor types it
    just explained) -- that's the "original KB match" this re-grounds
    against for an elaboration/comparison answer."""
    return kb_candidates_for(f"{prior_ai_response}\n{message_text}", top_k=top_k)


def is_continuation_of_prior_response(message_text: str, prior_ai_response: str) -> bool:
    """Does `message_text` refer back to, ask about, elaborate on, or
    compare something in `prior_ai_response` (a continuation of the same
    thread), or is it a genuinely new request? Runs on a follow-up after an
    AI answer, before classify_ticket -- classify_ticket judges each
    message's own classifiability, and a short follow-up fragment has
    almost no standalone content to classify confidently, which used to
    collapse decide_route's confidence and wrongly escalate an on-topic
    question. A positive result skips classify_ticket/decide_route for
    this turn."""
    return chat_response.is_continuation(
        ContinuationInput(message=message_text, prior_ai_response=prior_ai_response)
    ).is_continuation


def _history(state: TicketState) -> list[HistoryItem]:
    return [HistoryItem(**h) for h in state.get("customer_history", [])]


def _candidates(state: TicketState) -> list[KBCandidate]:
    return [KBCandidate(**c) for c in state.get("kb_candidates", [])]


def classify_ticket(state: TicketState) -> dict:
    candidates = state.get("kb_candidates", [])
    raw = categorize_op.categorize_ticket(
        CategorizeInput(
            ticket_text=state["ticket_text"],
            history=_history(state),
            kb_candidates=_candidates(state),
        )
    )
    llm_category, llm_confidence = raw.category, raw.confidence

    category, confidence = llm_category, llm_confidence
    top = candidates[0] if candidates else None
    if top and top["score"] >= kb_retrieval.STRONG_MATCH_SCORE:
        grounded_confidence = _kb_grounded_confidence(top["score"])
        if top["category"] == llm_category:
            # Retrieval agrees with the LLM's own pick and found a strong
            # direct match — trust it over a conservative raw confidence
            # number for a short/common phrasing.
            confidence = max(confidence, grounded_confidence)
        elif llm_confidence < CONFIDENCE_THRESHOLD:
            # LLM was unsure AND retrieval strongly points elsewhere —
            # trust the grounded KB match over an ungrounded guess.
            category, confidence = top["category"], grounded_confidence

    print(
        f"[classify_ticket] llm_category={llm_category!r} "
        f"llm_confidence={llm_confidence:.2f} -> category={category!r} "
        f"confidence={confidence:.2f} "
        f"(top_kb_match={top['intent'] if top else None}, "
        f"score={top['score'] if top else None})"
    )

    return {
        "category": category,
        "confidence": confidence,
        "language_tags": _detect_language_tags(state["ticket_text"]),
    }


def decide_route(state: TicketState) -> dict:
    confidence = state["confidence"]
    category = state["category"]
    tags = set(state.get("language_tags", []))
    clarification_attempts = state.get("clarification_attempts", 0)

    triggered_tags = tags & ESCALATION_LANGUAGE_TAGS
    high_risk_below_bar = (
        category in HIGH_RISK_CATEGORIES and confidence < HIGH_RISK_CONFIDENCE_THRESHOLD
    )
    below_base_bar = confidence < CONFIDENCE_THRESHOLD

    if triggered_tags:
        # Hard override: fires immediately regardless of confidence, category,
        # or how many clarification rounds have already happened.
        route, reason = "human", (
            f"escalation language tag(s) present: {sorted(triggered_tags)}"
        )
        clarification_attempts_out = clarification_attempts
    elif high_risk_below_bar:
        # Licensing/billing and data-retention ambiguity goes straight to
        # a human — no clarify round, regardless of clarification_attempts
        # so far. An AI probing "did you mean upgrading your license or
        # deleting your data?" on a high-risk ticket is exactly the wrong
        # place to let the AI keep guessing.
        route, reason = "human", (
            f"high-risk category {category!r} requires confidence >= "
            f"{HIGH_RISK_CONFIDENCE_THRESHOLD}, got {confidence:.2f} — "
            "escalating immediately, no clarification for licensing/data risk"
        )
        clarification_attempts_out = clarification_attempts
    elif below_base_bar and clarification_attempts < MAX_CLARIFICATION_ATTEMPTS:
        route, reason = "clarify", (
            f"confidence {confidence:.2f} for category {category!r} is below the "
            f"required bar (clarification attempt "
            f"{clarification_attempts + 1}/{MAX_CLARIFICATION_ATTEMPTS})"
        )
        clarification_attempts_out = clarification_attempts + 1
    elif below_base_bar:
        route, reason = "human", (
            f"still below the confidence bar after {MAX_CLARIFICATION_ATTEMPTS} "
            f"clarification attempts (confidence {confidence:.2f}, category "
            f"{category!r}) — forcing escalation"
        )
        clarification_attempts_out = clarification_attempts
    else:
        route, reason = "ai", (
            f"confidence {confidence:.2f} sufficient for category {category!r}"
        )
        clarification_attempts_out = clarification_attempts

    return {
        "route": route,
        "route_reason": reason,
        "clarification_attempts": clarification_attempts_out,
    }


def route_after_decide(state: TicketState) -> str:
    return {
        "ai": "lookup_order",
        "clarify": "ask_clarifying_question",
    }.get(state["route"], "human_queue")


def lookup_order(state: TicketState) -> dict:
    """Runs before generate_response, ai branch only. Grounds a ticket
    that names a specific order in a real (mock) order record instead of
    letting generate_response invent one. Site24x7 has no order/shipping
    category (_ORDER_RELATED_CATEGORIES is intentionally empty — see its
    definition above), so in practice this only fires on a literal
    ORD-XXXXX match in the text; it does not infer "this is order-related"
    from category the way it did for the old Bitext-era taxonomy.
    """
    match = _ORDER_ID_RE.search(state["ticket_text"])
    if not match and state.get("category") not in _ORDER_RELATED_CATEGORIES:
        return {"order_lookup": None}

    if not match:
        return {"order_lookup": {"not_found": True, "no_order_number_given": True}}

    order_id = match.group(0).upper()
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return {"order_lookup": {"not_found": True, "order_id": order_id}}
    return {"order_lookup": dict(row)}


def _format_order_context(order_lookup: dict | None) -> str:
    if order_lookup is None:
        return (
            "Order data: not applicable — this ticket doesn't appear to "
            "reference a specific order."
        )
    if order_lookup.get("no_order_number_given"):
        return (
            "Order data: this looks order-related but the customer gave no "
            "order number. Ask them for it — do not guess or assume any "
            "order status, items, or dates."
        )
    if order_lookup.get("not_found"):
        return (
            f"Order data: no record found for order {order_lookup['order_id']} "
            "in the order system. Tell the customer plainly that you can't "
            "verify that order number and ask them to double-check it — do "
            "NOT state or imply a status for it."
        )
    return (
        "Order data (verified, from the order system — use ONLY these "
        "facts; do not add a tracking number, delivery date, or any other "
        "detail not listed here):\n"
        f"  order_id: {order_lookup['order_id']}\n"
        f"  status: {order_lookup['status']}\n"
        f"  items: {order_lookup['items']}\n"
        f"  order_date: {order_lookup['order_date']}"
    )


def generate_response(state: TicketState) -> dict:
    reply = chat_response.answer(
        AnswerInput(
            ticket_text=state["ticket_text"],
            category=state.get("category"),
            history=_history(state),
            kb_candidates=_candidates(state),
            order_context=_format_order_context(state.get("order_lookup")),
            continuation=bool(state.get("continuation")),
            reply_format=state.get("reply_format", "chat"),
            customer_name=state.get("customer_name"),
        )
    )
    return {"ai_response": reply.text}


def ask_clarifying_question(state: TicketState) -> dict:
    """clarify branch — the ticket/transcript is too ambiguous to classify
    confidently. Asks one targeted question grounded in the actual
    candidate KB entries (e.g. which of two *real* Site24x7 topics
    applies) rather than a free-floating meta-question about categories
    that don't exist in this taxonomy. Stored via `ai_response` so it
    flows through the same message path as generate_response's output."""
    reply = chat_response.clarify(
        ClarifyInput(
            ticket_text=state["ticket_text"],
            category=state.get("category"),
            confidence=state.get("confidence", 0.0),
            kb_candidates=_candidates(state),
            reply_format=state.get("reply_format", "chat"),
            customer_name=state.get("customer_name"),
        )
    )
    return {"ai_response": reply.text}


def human_queue(state: TicketState) -> dict:
    """No-op terminal node for the human branch — main.py persists the
    ticket with route='human', status='open', assigned_technician_id=None."""
    return {}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
def build_graph():
    graph = StateGraph(TicketState)

    graph.add_node("fetch_customer_history", fetch_customer_history)
    graph.add_node("retrieve_kb_candidates", retrieve_kb_candidates)
    graph.add_node("classify_ticket", classify_ticket)
    graph.add_node("decide_route", decide_route)
    graph.add_node("lookup_order", lookup_order)
    graph.add_node("generate_response", generate_response)
    graph.add_node("ask_clarifying_question", ask_clarifying_question)
    graph.add_node("human_queue", human_queue)

    graph.set_entry_point("fetch_customer_history")
    graph.add_edge("fetch_customer_history", "retrieve_kb_candidates")
    graph.add_edge("retrieve_kb_candidates", "classify_ticket")
    graph.add_edge("classify_ticket", "decide_route")
    graph.add_conditional_edges(
        "decide_route",
        route_after_decide,
        {
            "lookup_order": "lookup_order",
            "ask_clarifying_question": "ask_clarifying_question",
            "human_queue": "human_queue",
        },
    )
    graph.add_edge("lookup_order", "generate_response")
    graph.add_edge("generate_response", END)
    graph.add_edge("ask_clarifying_question", END)
    graph.add_edge("human_queue", END)

    return graph.compile()
