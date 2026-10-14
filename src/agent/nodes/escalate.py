"""escalate — human handoff path.

Produces a structured summary for a human agent (not a customer-facing
response): ticket text, classified intent, confidence, and why
`decide_route` escalated it.
"""

from __future__ import annotations

from agent.state import EscalationSummary, TicketState


def escalate(state: TicketState) -> dict:
    summary = EscalationSummary(
        ticket_id=state["ticket_id"],
        instruction_text=state["instruction_text"],
        intent=state["intent"],
        confidence=state["confidence"],
        reason=state["route_reason"],
    )
    return {
        "escalation_summary": summary,
        "resolved_by": "human",
        "resolution_status": "pending",
        "trace": [*state.get("trace", []), "escalate"],
    }
