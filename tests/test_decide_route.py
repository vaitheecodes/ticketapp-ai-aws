"""Tests for backend/graph.py's decide_route — the three-way routing
policy (ai / clarify / human). Pure function, no LLM/DB calls, so these
run fast and deterministically.

Note: importing backend.graph does initialize an AzureOpenAI client at
module scope, which requires AZURE_OPENAI_ENDPOINT/API_KEY/DEPLOYMENT to
be set (loaded from .env) even though decide_route itself never calls it.

Run with: pytest tests/test_decide_route.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.graph import (  # noqa: E402
    CONFIDENCE_THRESHOLD,
    HIGH_RISK_CONFIDENCE_THRESHOLD,
    MAX_CLARIFICATION_ATTEMPTS,
    decide_route,
)

VAGUE_CONFIDENCE = CONFIDENCE_THRESHOLD - 0.05
CLEAR_CONFIDENCE = min(CONFIDENCE_THRESHOLD + 0.20, 1.0)


def _state(**overrides) -> dict:
    base = {
        "category": "MONITORING",
        "confidence": VAGUE_CONFIDENCE,
        "language_tags": [],
        "clarification_attempts": 0,
    }
    base.update(overrides)
    return base


def test_vague_ticket_clarifies_then_resolves_within_max_attempts():
    """Low confidence -> clarify; once a follow-up clears the bar, ai."""
    result = decide_route(_state(clarification_attempts=0))
    assert result["route"] == "clarify"
    assert result["clarification_attempts"] == 1

    # simulate the customer's reply resolving the ambiguity
    result = decide_route(
        _state(confidence=CLEAR_CONFIDENCE, clarification_attempts=result["clarification_attempts"])
    )
    assert result["route"] == "ai"
    # attempts count carries forward unchanged once resolved
    assert result["clarification_attempts"] == 1


def test_vague_ticket_never_clarifies_forces_human_on_attempt_after_max():
    """Confidence never clears the bar -> clarify up to MAX_CLARIFICATION_ATTEMPTS
    times, then decide_route forces human on the next call."""
    attempts = 0
    for expected_attempt_number in range(1, MAX_CLARIFICATION_ATTEMPTS + 1):
        result = decide_route(_state(clarification_attempts=attempts))
        assert result["route"] == "clarify", f"expected clarify on round {expected_attempt_number}"
        assert result["clarification_attempts"] == expected_attempt_number
        attempts = result["clarification_attempts"]

    # one more round still below the bar, with attempts already at the max
    result = decide_route(_state(clarification_attempts=attempts))
    assert result["route"] == "human"
    assert "forcing escalation" in result["route_reason"]
    # not incremented further — clarification is over, no more rounds to count
    assert result["clarification_attempts"] == MAX_CLARIFICATION_ATTEMPTS


def test_escalation_tag_overrides_mid_clarification_immediately():
    """An escalation language tag must win immediately, even partway through
    a clarification loop and even though confidence would otherwise clarify
    (not yet at MAX_CLARIFICATION_ATTEMPTS)."""
    result = decide_route(
        _state(
            confidence=CLEAR_CONFIDENCE,  # would otherwise be a clean "ai"
            language_tags=["offensive_language"],
            clarification_attempts=1,
        )
    )
    assert result["route"] == "human"
    assert "escalation language tag" in result["route_reason"]
    # hard override doesn't consume/advance a clarification attempt
    assert result["clarification_attempts"] == 1


def test_high_risk_category_below_high_risk_bar_escalates_immediately_no_clarify():
    """A high-risk category (ACCOUNT/DATA) below the stricter 90% bar must
    skip the clarify loop entirely and escalate straight to a human —
    licensing/billing or data-retention ambiguity is exactly the case
    where an AI shouldn't be probing first, even though this confidence
    clears the base 75% threshold that would let a non-high-risk category
    clarify."""
    confidence = HIGH_RISK_CONFIDENCE_THRESHOLD - 0.05
    assert confidence >= CONFIDENCE_THRESHOLD  # clears base bar, not the high-risk one
    result = decide_route(
        _state(category="ACCOUNT", confidence=confidence, clarification_attempts=0)
    )
    assert result["route"] == "human"
    assert result["clarification_attempts"] == 0


def test_vague_high_risk_ticket_escalates_on_first_call_without_clarifying():
    """A genuinely vague (low-confidence) high-risk-category ticket must
    escalate to human on the very first decide_route call — no clarify
    round at all, clarification_attempts stays at 0."""
    result = decide_route(
        _state(category="DATA", confidence=0.5, clarification_attempts=0)
    )
    assert result["route"] == "human"
    assert "no clarification for licensing/data risk" in result["route_reason"]
    assert result["clarification_attempts"] == 0


def test_high_risk_category_stays_immediate_human_even_mid_clarification():
    """If a non-high-risk clarify loop reveals the ticket is actually
    high-risk (e.g. a vague MONITORING ticket turns out to be about a
    DATA retention request) with confidence still below the high-risk
    bar, it must escalate immediately rather than continuing to clarify
    just because clarification_attempts hasn't hit the max yet."""
    result = decide_route(
        _state(
            category="DATA",
            confidence=HIGH_RISK_CONFIDENCE_THRESHOLD - 0.1,
            clarification_attempts=1,
        )
    )
    assert result["route"] == "human"
    # attempts count is left as-is — this isn't a clarify round
    assert result["clarification_attempts"] == 1


def test_clear_confidence_routes_to_ai_without_clarifying():
    result = decide_route(_state(confidence=CLEAR_CONFIDENCE, clarification_attempts=0))
    assert result["route"] == "ai"
    assert result["clarification_attempts"] == 0
