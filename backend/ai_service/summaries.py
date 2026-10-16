"""summarize_conversation: the two chat summaries.

- summarize_resolution: problem / troubleshooting performed / solution,
  for the internal review record of an AI-resolved chat.
- summarize_escalation: issue / tried / findings / unresolved questions /
  suggested next steps, for the technician who picks up an escalated chat.

Both only restate what the transcript supports: the app isn't connected
to any monitoring system, so the model must not claim it checked one.
Each has a deterministic fallback for when the provider is down, so a
resolution or escalation never fails (or silently changes) because of AI.
"""

from __future__ import annotations

from backend.ai_service import provider
from backend.ai_service.schemas import ChatResolutionSummary, EscalationSummary

_GROUNDING = (
    "Only state what the transcript actually shows. Do not invent "
    "troubleshooting results, error messages, or checks, and never claim "
    "anyone inspected a monitoring system, server, or account — this "
    "application has no access to any. Keep each item to one short sentence."
)


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


def summarize_escalation(transcript: str, reason: str) -> EscalationSummary:
    summary = provider.complete_json(
        "You write the hand-off brief for a Site24x7 support technician "
        "taking over a chat the AI assistant could not resolve. Fields: "
        "issue (the customer's problem), tried (what was already suggested "
        "or attempted), findings (facts the customer reported: versions, "
        "errors, monitor names, timings), unresolved_questions (what is "
        "still unknown), suggested_next_steps (concrete next actions for "
        "the technician, phrased as things to check or ask — not results). "
        "The technician must not need to ask the customer anything the "
        f"transcript already answers. {_GROUNDING}",
        {"escalation_reason": reason, "transcript": transcript},
        EscalationSummary,
    )
    return summary.model_copy(update={"source": "ai"})


def fallback_resolution(issue: str, last_ai_reply: str | None) -> ChatResolutionSummary:
    return ChatResolutionSummary(
        problem=issue,
        troubleshooting=[],
        solution=(last_ai_reply or "See transcript.")[:2000],
        source="fallback",
    )


def fallback_escalation(issue: str, reason: str) -> EscalationSummary:
    return EscalationSummary(
        issue=issue,
        findings=[f"Escalated because: {reason}"],
        unresolved_questions=["AI summary unavailable; read the chat transcript."],
        source="fallback",
    )
