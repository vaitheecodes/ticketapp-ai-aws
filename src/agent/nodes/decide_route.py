"""decide_route — the routing brain.

Not a model call at any stage: this is deterministic policy over
signals already on the state. Considers, in order:

  1. category risk — ACCOUNT/DATA need a higher confidence bar than the
     base threshold before auto-resolving (wrong auto-response on
     licensing/billing changes or data retention/deletion is costlier
     than on e.g. MONITORING/ALERTS)
  2. escalation-triggering language tags — offensive language,
     coordinated/compound phrasing, negation (the `W`/`C`/`N` flags)
  3. plain classification confidence vs the base threshold

Writes `route` + a human-readable `route_reason` onto the state; the
graph's conditional edge reads `route` to pick the next node.
"""

from __future__ import annotations

from agent.state import (
    CONFIDENCE_THRESHOLD,
    ESCALATION_LANGUAGE_TAGS,
    HIGH_RISK_CATEGORIES,
    HIGH_RISK_CONFIDENCE_THRESHOLD,
    TicketState,
)


def decide_route(state: TicketState) -> dict:
    confidence = state["confidence"]
    category = state["category"]
    tags = set(state.get("language_tags", []))

    triggered_tags = tags & ESCALATION_LANGUAGE_TAGS
    if triggered_tags:
        route, reason = "escalate", (
            f"escalation language tag(s) present: {sorted(triggered_tags)}"
        )
    elif category in HIGH_RISK_CATEGORIES and confidence < HIGH_RISK_CONFIDENCE_THRESHOLD:
        route, reason = "escalate", (
            f"high-risk category {category!r} requires confidence >= "
            f"{HIGH_RISK_CONFIDENCE_THRESHOLD}, got {confidence:.2f}"
        )
    elif confidence < CONFIDENCE_THRESHOLD:
        route, reason = "escalate", (
            f"confidence {confidence:.2f} below threshold {CONFIDENCE_THRESHOLD}"
        )
    else:
        route, reason = "auto_resolve", (
            f"confidence {confidence:.2f} sufficient for category {category!r}"
        )

    return {
        "route": route,
        "route_reason": reason,
        "trace": [*state.get("trace", []), "decide_route"],
    }


def route_after_decide(state: TicketState) -> str:
    """Conditional-edge selector: reads the route decide_route wrote."""
    return state["route"]
