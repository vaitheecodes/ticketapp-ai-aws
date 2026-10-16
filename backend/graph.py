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

The Azure OpenAI client is initialized once, here, at module load — this
is the one place a Phase 2 provider swap would touch.
"""

from __future__ import annotations

import json
import os
import re
from typing import Literal, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from openai import AzureOpenAI
from typing_extensions import NotRequired

from backend import db, kb_retrieval

load_dotenv()

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
CATEGORIES = [
    "ACCOUNT", "AGENT", "ALERTS", "APM", "APPLICATION_MONITORING", "CLOUD",
    "CONTAINERS", "DATA", "INTEGRATION", "LOGS", "MONITORING", "NETFLOW",
    "REPORTING", "SERVER_MONITORING", "SNMP", "SUPPORT", "WEB_MONITORING",
]

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
# Azure OpenAI client — single init point for a later provider swap
# ---------------------------------------------------------------------------
_client = AzureOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
)
_DEPLOYMENT = os.environ["AZURE_OPENAI_DEPLOYMENT"]


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
    query = f"{prior_ai_response}\n{message_text}"
    return _kb_candidates_to_dicts(kb_retrieval.retrieve_candidates(query, top_k=top_k))


def is_continuation_of_prior_response(message_text: str, prior_ai_response: str) -> bool:
    """Cheap, targeted LLM check: does `message_text` refer back to, ask
    about, elaborate on, or compare something in `prior_ai_response` (a
    continuation of the same thread), or is it a genuinely new/
    different request? Runs on a chat follow-up to an already-resolved
    ticket, before classify_ticket -- classify_ticket judges each
    message's own classifiability, and a short follow-up fragment has
    almost no standalone content to classify confidently against the
    category/intent taxonomy, which used to collapse decide_route's
    confidence and wrongly escalate an on-topic question the
    conversation already makes perfectly answerable. A positive result
    here skips classify_ticket/decide_route entirely for this turn."""
    system_prompt = (
        "Decide whether the customer's NEW message is a CONTINUATION of "
        "this support conversation -- it refers back to, asks about, "
        "elaborates on, or compares something in the agent's last reply "
        "below -- or a NEW, unrelated request that has nothing to do "
        "with that reply. Respond with strict JSON only: "
        '{"is_continuation": true|false}'
    )
    user_prompt = (
        f"Agent's last reply:\n{prior_ai_response}\n\n"
        f"Customer's new message:\n{message_text}"
    )
    completion = _client.chat.completions.create(
        model=_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    parsed = json.loads(completion.choices[0].message.content)
    return bool(parsed.get("is_continuation", False))


def _format_kb_candidates_for_classification(candidates: list[dict]) -> str:
    if not candidates:
        return "No close KB matches were found for this ticket text."
    lines = ["Closest matching real Site24x7 KB entries, ranked by relevance:"]
    for c in candidates:
        lines.append(
            f'  - [{c["category"]} / {c["intent"]}] example question: '
            f'"{c["instruction"]}" (match score {c["score"]:.2f})'
        )
    return "\n".join(lines)


def classify_ticket(state: TicketState) -> dict:
    history_summary = "\n".join(
        f"- [{h['category'] or 'uncategorized'}, {h['status']}] {h['ticket_text'][:120]}"
        for h in state.get("customer_history", [])
    ) or "(no prior tickets)"
    candidates = state.get("kb_candidates", [])

    system_prompt = (
        "You classify support tickets for Site24x7 (an infrastructure "
        "monitoring product) into exactly one of these categories: "
        f"{', '.join(CATEGORIES)}.\n"
        "Tickets are about monitor setup/troubleshooting, alerting, "
        "integrations, agent installation, licensing, or data retention — "
        "not e-commerce orders, shipping, or refunds.\n"
        "You are given a ranked list of the closest real KB entries below "
        "— treat a close wording match there as strong evidence for that "
        "entry's category, even for a short or casually-phrased ticket.\n"
        "Respond with strict JSON only: "
        '{"category": "<ONE_OF_THE_CATEGORIES>", "confidence": <0.0-1.0>}'
    )
    user_prompt = (
        f"Customer's recent ticket history:\n{history_summary}\n\n"
        f"{_format_kb_candidates_for_classification(candidates)}\n\n"
        f"New ticket text:\n{state['ticket_text']}"
    )

    completion = _client.chat.completions.create(
        model=_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    parsed = json.loads(completion.choices[0].message.content)
    llm_category = parsed.get("category") if parsed.get("category") in CATEGORIES else "SUPPORT"
    llm_confidence = float(parsed.get("confidence", 0.5))

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


def _format_kb_grounding(candidates: list[dict]) -> str:
    if not candidates:
        return (
            "KB match: none found for this ticket. Don't invent a specific "
            "Site24x7 feature, setting, or behavior you're not sure of — "
            "keep the answer general or say it'll need a human to check."
        )
    top = candidates[0]
    return (
        f"Closest matching KB entry ([{top['category']} / {top['intent']}], "
        f"match score {top['score']:.2f}):\n"
        f"  Typical question: {top['instruction']}\n"
        f"  Canonical guidance: {top['response']}\n"
        "Base your answer on this guidance, personalized to the customer's "
        "own wording — don't contradict it, and don't invent Site24x7 "
        "behavior beyond what it and the ticket data below support."
    )


def generate_response(state: TicketState) -> dict:
    history_summary = "\n".join(
        f"- [{h['category'] or 'uncategorized'}] {h['ticket_text'][:120]}"
        for h in state.get("customer_history", [])
    ) or "(no prior tickets)"

    continuation_note = (
        " The ticket text below is the full conversation so far, and the "
        "customer's latest message is a follow-up to your own most "
        "recent reply in it (e.g. asking you to elaborate, clarify, or "
        "compare something you just said) — answer that latest message "
        "directly, building on your own prior reply, not as an isolated "
        "new request."
        if state.get("continuation")
        else ""
    )
    system_prompt = (
        "You are a helpful customer support agent for Site24x7 (an "
        "infrastructure monitoring product). Write a clear, concise, "
        f"friendly reply that resolves the customer's request.{continuation_note}\n\n"
        "CRITICAL — grounding rule: only state specific facts (order "
        "status, monitor/alert behavior, account details, etc.) that were "
        "actually given to you below or in the ticket text. Never invent "
        "a plausible-sounding tracking number, delivery date, refund "
        "amount, or Site24x7 feature/setting you were not given. If "
        "answering the request would require a specific fact you don't "
        "have, say plainly that you don't have access to it and ask the "
        "customer for it (or say it'll need a human to check), instead of "
        "guessing."
    )
    user_prompt = (
        f"Category: {state['category']}\n"
        f"Customer's recent ticket history:\n{history_summary}\n\n"
        f"{_format_kb_grounding(state.get('kb_candidates', []))}\n\n"
        f"{_format_order_context(state.get('order_lookup'))}\n\n"
        f"Ticket text:\n{state['ticket_text']}"
    )

    completion = _client.chat.completions.create(
        model=_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )
    return {"ai_response": completion.choices[0].message.content}


def ask_clarifying_question(state: TicketState) -> dict:
    """clarify branch — the ticket/transcript is too ambiguous to classify
    confidently. Ask one targeted question grounded in the actual
    candidate KB entries retrieve_kb_candidates found (e.g. which of two
    *real* Site24x7 topics applies) rather than a free-floating meta-
    question about categories/features that don't exist in this
    taxonomy. Stored via `ai_response` so it flows through the same
    ticket_messages/TicketChat.jsx path as generate_response's output —
    no separate plumbing needed for it to show up in the chat.
    """
    candidates = state.get("kb_candidates", [])
    if candidates:
        candidate_block = "\n".join(
            f'  {i + 1}. [{c["category"]} / {c["intent"]}]: e.g. "{c["instruction"]}"'
            for i, c in enumerate(candidates[:3])
        )
        grounding = (
            "Here are the closest real Site24x7 support topics this ticket "
            f"could match, ranked by relevance:\n{candidate_block}\n\n"
            "Ask exactly ONE short question that would tell you which of "
            "THESE SPECIFIC topics applies (or that none of them do). Refer "
            "to what they're actually about — do not invent categories, "
            "features, or distinctions (e.g. 'plans', 'subscriptions') that "
            "aren't reflected in this list."
        )
    else:
        grounding = (
            "No close match was found in the Site24x7 KB for this ticket. "
            "Ask exactly ONE short question that would help identify what "
            "the customer actually needs — do not guess at a specific "
            "feature or invent a distinction that may not apply."
        )
    system_prompt = (
        "A customer support ticket for Site24x7 (an infrastructure "
        "monitoring product) is too ambiguous to classify confidently. "
        f"{grounding} Do not answer the ticket, do not ask a generic 'can "
        "you tell me more?', and do not assume details the customer "
        "hasn't given you."
    )
    user_prompt = (
        f"Best-guess category so far: {state['category']} "
        f"(confidence {state['confidence']:.2f})\n\n"
        f"Conversation so far:\n{state['ticket_text']}"
    )

    completion = _client.chat.completions.create(
        model=_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )
    return {"ai_response": completion.choices[0].message.content}


def summarize_case(transcript_text: str) -> str:
    """Used when an ongoing AI chat escalates mid-conversation — condenses
    the transcript into a case brief for the human agent, since agents
    otherwise only ever see the ticket's original opening line."""
    completion = _client.chat.completions.create(
        model=_DEPLOYMENT,
        messages=[
            {
                "role": "system",
                "content": (
                    "Summarize this customer support conversation into a short "
                    "case brief for a human agent: what the customer wants, key "
                    "details mentioned (order numbers, dates, amounts), and why "
                    "it needs a human. 3-5 sentences, no preamble."
                ),
            },
            {"role": "user", "content": transcript_text},
        ],
        temperature=0,
    )
    return completion.choices[0].message.content


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
