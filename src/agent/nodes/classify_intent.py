"""classify_intent — stub classifier.

Stage 1: a tiny keyword lookup standing in for real classification, so
the graph shape and `decide_route` logic can be exercised without a
model call. Two real options to wire in later (tradeoffs noted in the
README once we get there):

  - embedding similarity against KB exemplars per intent (cheap, fast,
    needs a decent embedding model + a well-covered KB)
  - a single Bedrock Claude call with the 27-intent list as a
    classification prompt (more robust to phrasing, higher latency/cost
    per ticket)

Unmatched text falls back to a low-confidence guess so `decide_route`
has a realistic low-confidence path to exercise even in the stub.
"""

from __future__ import annotations

from agent.state import TicketState

# keyword -> (category, intent, confidence). Stub only — deliberately
# covers just enough intents to demonstrate routing across categories.
_KEYWORD_RULES: list[tuple[str, str, str, float]] = [
    ("is down", "MONITORING", "monitor_down", 0.93),
    ("not discovered", "MONITORING", "monitor_not_discovered", 0.88),
    ("monitor status", "MONITORING", "monitor_status", 0.91),
    ("configure an alert", "ALERTS", "alert_configuration", 0.94),
    ("no alert", "ALERTS", "alert_not_triggered", 0.87),
    ("false alert", "ALERTS", "false_alert", 0.85),
    ("threshold", "ALERTS", "monitor_threshold", 0.90),
    ("install the", "AGENT", "agent_installation", 0.92),
    ("agent is not communicating", "AGENT", "agent_not_reporting", 0.89),
    ("kubernetes", "CONTAINERS", "kubernetes_monitoring", 0.91),
    ("docker", "CONTAINERS", "docker_monitoring", 0.90),
    ("license", "ACCOUNT", "licensing", 0.80),
    ("data retention", "DATA", "data_retention", 0.82),
    ("integrat", "INTEGRATION", "integrations", 0.87),
    ("speak to a human", "SUPPORT", "contact_support", 0.85),
]

_FALLBACK_CATEGORY, _FALLBACK_INTENT, _FALLBACK_CONFIDENCE = (
    "SUPPORT", "contact_support", 0.45,
)


def classify_intent(state: TicketState) -> dict:
    text = state["instruction_text"].lower()
    for keyword, category, intent, confidence in _KEYWORD_RULES:
        if keyword in text:
            return {
                "category": category,
                "intent": intent,
                "confidence": confidence,
                "trace": [*state.get("trace", []), "classify_intent"],
            }

    return {
        "category": _FALLBACK_CATEGORY,
        "intent": _FALLBACK_INTENT,
        "confidence": _FALLBACK_CONFIDENCE,
        "trace": [*state.get("trace", []), "classify_intent"],
    }
