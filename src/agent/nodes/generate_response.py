"""generate_response — auto-resolve path.

Stage 1: formats the stub KB entry's canonical response, no model call.
Stage 3 replaces this with a Bedrock Claude call grounded in the
retrieved KB entry and personalized to this ticket's specific entities
(the RAG step) — prompt template will live in src/agent/prompts/.
"""

from __future__ import annotations

from agent.state import TicketState


def generate_response(state: TicketState) -> dict:
    kb_entry = state["kb_entry"]
    response_text = kb_entry["canonical_response"].replace(
        "{{steps}}", f"resolving your {state['intent'].replace('_', ' ')} request"
    )
    return {
        "response_text": response_text,
        "resolved_by": "ai",
        "resolution_status": "resolved",
        "trace": [*state.get("trace", []), "generate_response"],
    }
