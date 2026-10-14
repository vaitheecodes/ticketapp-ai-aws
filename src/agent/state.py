"""Shared state schema + routing config for the ticket-resolution graph.

Stage 1 (current): stub nodes only, no Bedrock calls — this file defines
the state shape and the routing thresholds that `decide_route` will use,
so the graph shape and routing logic can be inspected on dummy data
before any model call is wired in.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from typing_extensions import NotRequired

# ---------------------------------------------------------------------------
# Routing config
# ---------------------------------------------------------------------------
# Below this classification confidence, escalate regardless of category.
CONFIDENCE_THRESHOLD = 0.75

# Language tags (decoded from the `flags` column, see src/pipeline/config.py)
# that push a ticket toward escalation even at high confidence: offensive
# language, coordinated/compound phrasing, and negation all make a
# templated auto-response more likely to misfire.
ESCALATION_LANGUAGE_TAGS = {"offensive_language", "coordinated_syntax", "negation"}

# Categories where a wrong auto-resolution is costly (licensing/billing
# changes, data retention/deletion) — held to a stricter confidence bar
# than the base threshold. See src/pipeline/config.py:HIGH_RISK_CATEGORIES.
HIGH_RISK_CATEGORIES = {"ACCOUNT", "DATA"}
HIGH_RISK_CONFIDENCE_THRESHOLD = 0.90


class KBEntry(TypedDict):
    """A retrieved knowledge-base entry for the classified intent."""

    intent: str
    canonical_response: str
    entity_slots: list[str]
    source_ticket_id: str


class EscalationSummary(TypedDict):
    """Structured handoff summary for a human agent."""

    ticket_id: str
    instruction_text: str
    intent: str
    confidence: float
    reason: str


class TicketState(TypedDict):
    # -- ingest_ticket ------------------------------------------------
    ticket_id: str
    channel: str
    customer_id: str
    instruction_text: str
    language_tags: NotRequired[list[str]]

    # -- classify_intent -----------------------------------------------
    category: NotRequired[str]
    intent: NotRequired[str]
    confidence: NotRequired[float]

    # -- retrieve_knowledge ----------------------------------------------
    kb_entry: NotRequired[KBEntry | None]

    # -- decide_route -----------------------------------------------------
    route: NotRequired[Literal["auto_resolve", "escalate"]]
    route_reason: NotRequired[str]

    # -- generate_response / escalate --------------------------------------
    response_text: NotRequired[str]
    escalation_summary: NotRequired[EscalationSummary]

    # -- log_outcome ------------------------------------------------------
    resolved_by: NotRequired[Literal["ai", "human"]]
    resolution_status: NotRequired[Literal["resolved", "escalated", "pending"]]

    # trace of node names visited, in order — useful for debugging/demo output
    trace: NotRequired[list[str]]
