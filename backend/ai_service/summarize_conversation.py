"""summarize_conversation: the two chat summaries.

- summarize_resolution: problem / troubleshooting performed / solution,
  for the internal review record of an AI-resolved chat.
- summarize_escalation: issue / tried / findings / unresolved questions,
  for the technician who picks up an escalated chat (next steps come from
  suggest_steps).

Both only restate what the transcript supports. Each has a deterministic
fallback for when the provider is down, so a resolution or escalation
never fails (or silently changes) because of the AI.

tidy_handoff then cleans any hand-off brief (chat or ticket) in code:
placeholder items ("N/A", "none") are dropped, "tried" becomes "Nothing
stated yet" when the customer said nothing about it, and findings that
only restate the issue are dropped.
"""

from __future__ import annotations

import re

from backend.ai_service import provider
from backend.ai_service._format import GROUNDING_RULES
from backend.ai_service.grounding import content_words
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
    "Fields: issue (the customer's problem in one sentence, in their terms); "
    "tried (only what the customer says they already tried themselves; an empty "
    "list if they said nothing about it, never 'N/A'); findings (new facts the "
    "customer stated beyond the issue sentence: exact error text, versions, which "
    "login or screen, timings, what changed; never restate the issue; an empty "
    "list if there are none); unresolved_questions (the specific unknowns a "
    "technician needs answered, e.g. which login (web console or mobile app), the "
    "exact text on the screen, what a vague phrase such as 'changing license' "
    "means; never 'N/A'). The technician must not need to ask the customer "
    "anything the conversation already answers."
)
NOTHING_TRIED = "Nothing stated yet"
# A placeholder item: "N/A", "None.", "Not mentioned", "-".
_EMPTY_ITEM = re.compile(
    r"^\W*(n/?a|none|nothing|null|not applicable|not (stated|mentioned|provided|specified)|unknown"
    r"|no (information|details?|attempts?)( (provided|given|yet))?)\W*$|^\W*$", re.IGNORECASE)
# A finding whose words are (nearly) all in the issue restates it.
RESTATEMENT = 0.8


def _restates(item: str, base_words: set[str]) -> bool:
    words = content_words(item)
    return not words or len(words & base_words) / len(words) >= RESTATEMENT


def tidy_handoff(summary: HandoffSummary, customer_text: str = "") -> HandoffSummary:
    """The brief without placeholder items or restated findings; "tried"
    says "Nothing stated yet" rather than nothing."""
    def keep(items):
        return [i.strip() for i in items if not _EMPTY_ITEM.match(i)]

    base = content_words(f"{summary.issue}\n{customer_text.splitlines()[0] if customer_text else ''}")
    return summary.model_copy(update={
        "tried": keep(summary.tried) or [NOTHING_TRIED],
        "findings": [f for f in keep(summary.findings) if f.startswith("Escalated because:")
                     or not _restates(f, base)],
        "unresolved_questions": keep(summary.unresolved_questions),
    })


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
        tried=[NOTHING_TRIED],
        findings=[f"Escalated because: {reason}"],
        unresolved_questions=["AI summary unavailable; read the conversation."],
        source="fallback",
    )
