"""summarize_conversation: the two chat summaries.

- summarize_resolution: problem / troubleshooting performed / solution,
  for the internal review record of an AI-resolved chat.
- summarize_escalation: issue / tried / findings / unresolved questions,
  for the technician who picks up an escalated chat (next steps come from
  suggest_steps).

Both only restate what the transcript supports. Each has a deterministic
fallback for when the provider is down, so a resolution or escalation
never fails (or silently changes) because of the AI.
"""

from __future__ import annotations

from backend.ai_service import provider
from backend.ai_service._format import GROUNDING_RULES
from backend.ai_service.schemas import ChatResolutionSummary, HandoffSummary

_GROUNDING = f"{GROUNDING_RULES} Keep each item to one short sentence."


def summarize_resolution(transcript: str) -> ChatResolutionSummary:
    summary = provider.complete_json(
        "You summarize a Site24x7 support chat that the customer confirmed "
        "as resolved by the AI assistant, for an internal reviewer. Fields: "
        "problem (what the customer needed), troubleshooting (steps the "
        "assistant suggested or the customer tried, in order), solution "
        f"(what resolved it). {_GROUNDING}",
        {"transcript": transcript},
        ChatResolutionSummary,
    )
    return summary.model_copy(update={"source": "ai"})


HANDOFF_FIELDS = (
    "Fields: issue (the customer's problem), tried (what was already "
    "suggested or attempted), findings (facts the customer reported: "
    "versions, errors, monitor names, timings), unresolved_questions (what "
    "is still unknown). The technician must not need to ask the customer "
    "anything the conversation already answers."
)


def summarize_escalation(transcript: str, reason: str) -> HandoffSummary:
    summary = provider.complete_json(
        "You write the hand-off brief for a Site24x7 support technician "
        f"taking over a chat the AI assistant could not resolve. {HANDOFF_FIELDS} "
        f"{_GROUNDING}",
        {"escalation_reason": reason, "transcript": transcript},
        HandoffSummary,
    )
    return summary.model_copy(update={"source": "ai"})


def fallback_resolution(issue: str, last_ai_reply: str | None) -> ChatResolutionSummary:
    return ChatResolutionSummary(
        problem=issue,
        troubleshooting=[],
        solution=(last_ai_reply or "See transcript.")[:2000],
        source="fallback",
    )


def fallback_handoff(issue: str, reason: str) -> HandoffSummary:
    return HandoffSummary(
        issue=issue,
        findings=[f"Escalated because: {reason}"],
        unresolved_questions=["AI summary unavailable; read the conversation."],
        source="fallback",
    )
