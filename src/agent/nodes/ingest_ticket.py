"""ingest_ticket — entry node.

Stage 1 stub: normalizes the incoming ticket fields and starts the trace.
No validation/enrichment logic yet beyond defaulting `language_tags`
(real incoming tickets won't carry pre-decoded flags the way the demo
dataset does — a future stage will need its own light language-tag
detection, e.g. a profanity/negation check, ahead of `decide_route`).
"""

from __future__ import annotations

from agent.state import TicketState


def ingest_ticket(state: TicketState) -> dict:
    return {
        "language_tags": state.get("language_tags", []),
        "trace": [*state.get("trace", []), "ingest_ticket"],
    }
