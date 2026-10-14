"""retrieve_knowledge — stub retrieval.

Stage 1: returns a canned KB entry keyed by intent instead of querying
the real KB split. Stage 2 will replace this with a lookup against the
~80% KB portion of `tickets`/`resolutions` (split by intent, held-out
20% reserved as test tickets — see CLAUDE.md) so the agent never sees a
held-out ticket's own gold response.
"""

from __future__ import annotations

from agent.state import KBEntry, TicketState

_STUB_KB: dict[str, KBEntry] = {}


def _kb_entry_for(intent: str) -> KBEntry:
    if intent not in _STUB_KB:
        _STUB_KB[intent] = KBEntry(
            intent=intent,
            canonical_response=(
                f"[stub KB entry for intent={intent}] Thanks for reaching "
                "out — here's how we can help with that: {{steps}}."
            ),
            entity_slots=["order_number", "customer_name"],
            source_ticket_id="KB-STUB",
        )
    return _STUB_KB[intent]


def retrieve_knowledge(state: TicketState) -> dict:
    kb_entry = _kb_entry_for(state["intent"])
    return {
        "kb_entry": kb_entry,
        "trace": [*state.get("trace", []), "retrieve_knowledge"],
    }
