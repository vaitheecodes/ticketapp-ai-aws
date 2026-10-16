"""Rendering shared inputs as data-block text."""

from __future__ import annotations

from backend.ai_service.schemas import HistoryItem, KBCandidate

GROUNDING_RULES = (
    "Only state what the provided data supports. Do not invent "
    "troubleshooting results, error messages, account details, or checks, "
    "and never claim that anyone inspected a monitoring system, server, or "
    "account: this application has no access to any of them."
)


def history_text(history: list[HistoryItem], with_status: bool = False) -> str:
    if not history:
        return "(no prior tickets)"
    return "\n".join(
        f"- [{h.category or 'uncategorized'}"
        f"{', ' + h.status if with_status and h.status else ''}] {h.ticket_text[:120]}"
        for h in history
    )


def candidates_text(candidates: list[KBCandidate]) -> str:
    if not candidates:
        return "(no close KB matches)"
    return "\n".join(
        f'- [{c.category} / {c.intent}] example question: "{c.instruction}" '
        f"(match score {c.score:.2f})"
        for c in candidates
    )


def kb_guidance_text(candidates: list[KBCandidate]) -> str:
    if not candidates:
        return "(no KB match)"
    top = candidates[0]
    return (
        f"[{top.category} / {top.intent}] (match score {top.score:.2f})\n"
        f"Typical question: {top.instruction}\n"
        f"Canonical guidance: {top.response}"
    )
