"""summarize_ticket: the hand-off brief for an email-style ticket the AI
was handling when it went to a person (decide_route, a hand-off signal,
or the customer reopening an AI-resolved ticket). Same shape as a chat
escalation brief so the technician's AI panel reads the same either way.

The thread passed in must be the public conversation only (no internal
notes): see backend/main.py:_transcript_for.
"""

from __future__ import annotations

from backend.ai_service import provider
from backend.ai_service.schemas import HandoffSummary
from backend.ai_service.summarize_conversation import _GROUNDING, HANDOFF_FIELDS


def summarize_ticket(thread: str, reason: str) -> HandoffSummary:
    summary = provider.complete_json(
        "You write the hand-off brief for a Site24x7 support technician taking "
        "over a support ticket the AI assistant was handling. "
        f"{HANDOFF_FIELDS} {_GROUNDING}",
        {"handoff_reason": reason, "ticket_thread": thread},
        HandoffSummary,
    )
    return summary.model_copy(update={"source": "ai"})
