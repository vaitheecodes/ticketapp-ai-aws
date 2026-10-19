"""draft_post_incident: the customer-facing post-incident summary (plus a
short internal one) for a confirmed incident staff are resolving.

The model writes only three plain-language parts — what happened, what
was fixed, what customers should do — from the confirmed incident's own
analysis and the staff resolution note. Code assembles the summary and
adds the "When" line (start to resolution, IST) and the "Cause" line (the
staff-entered root cause verbatim, or "still being reviewed"). A semantic
check rejects model text that contains a clock time, a date, a weekday or
relative day, or a cause claim, so none of those can be invented; one
retry, then AIError (nothing is saved as a draft and nothing is sent).
"""

from __future__ import annotations

import re

from backend.ai_service import provider
from backend.ai_service._format import GROUNDING_RULES
from backend.ai_service.schemas import PostIncidentDraft, PostIncidentFacts

CAUSE_UNDER_REVIEW = "The cause is still being reviewed. We'll share more if it affects you."

_TIME_OR_DATE = re.compile(
    r"\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b|\b(?:19|20)\d{2}\b|\butc\b|\bist\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|yesterday|today|tonight"
    r"|this morning|last night)\b",
    re.IGNORECASE,
)
_CAUSE_CLAIM = re.compile(
    r"\b(?:caused by|due to|because of|resulted from|root cause|the cause)\b", re.IGNORECASE
)


def _check(draft: PostIncidentDraft) -> None:
    for name in ("what_happened", "what_was_fixed", "what_customers_should_do", "internal_summary"):
        text = getattr(draft, name)
        if _TIME_OR_DATE.search(text):
            raise ValueError(f"{name} mentions a time or date")
        if _CAUSE_CLAIM.search(text):
            raise ValueError(f"{name} states a cause")


def compose_customer_summary(draft: PostIncidentDraft, facts: PostIncidentFacts) -> str:
    cause = (facts.root_cause or "").strip() or CAUSE_UNDER_REVIEW
    return (
        f"What happened\n{draft.what_happened.strip()}\n\n"
        f"When\nFrom {facts.started_at_ist} to {facts.resolved_at_ist}.\n\n"
        f"What we fixed\n{draft.what_was_fixed.strip()}\n\n"
        f"Cause\n{cause}\n\n"
        f"What you need to do\n{draft.what_customers_should_do.strip()}"
    )


def compose_internal_summary(draft: PostIncidentDraft, facts: PostIncidentFacts) -> str:
    return (
        f"{draft.internal_summary.strip()}\n\n"
        f"Window: {facts.started_at_ist} to {facts.resolved_at_ist}. "
        f"Root cause: {(facts.root_cause or '').strip() or 'not yet determined'}. "
        f"Resolution: {facts.resolution_note.strip()} "
        f"Affected: {facts.affected_customer_count} customers, {facts.affected_ticket_count} tickets."
    )


def draft_post_incident(facts: PostIncidentFacts) -> tuple[str, str]:
    """Returns (customer_summary, internal_summary)."""
    data = {
        "incident_title": facts.title,
        "category": facts.category or "unknown",
        "symptoms": "\n".join(f"- {s}" for s in facts.common_symptoms) or "(none recorded)",
        "affected_services": "\n".join(f"- {s}" for s in facts.affected_services) or "(none recorded)",
        "observed_facts": "\n".join(f"- {s}" for s in facts.observed_facts) or "(none recorded)",
        "resolution_note": facts.resolution_note,
        "scale": f"{facts.affected_customer_count} customers, {facts.affected_ticket_count} tickets",
    }
    draft = provider.complete_json(
        "You write a short post-incident summary for Site24x7 customers who were "
        "affected by an incident that is now resolved. Use ONLY the facts in the "
        "data blocks: the incident's symptoms, affected services, observed facts and "
        "the staff resolution note. Fields: what_happened (one or two plain sentences "
        "on what customers experienced), what_was_fixed (what the team did, from the "
        "resolution note only), what_customers_should_do (concrete next steps, or "
        "'Nothing — your monitoring is back to normal.' if none apply), "
        "internal_summary (two or three sentences for the support team). Do NOT "
        "mention any time, date, weekday or 'yesterday'/'today' (the times are added "
        "separately), and do NOT state or guess any cause (the cause line is added "
        "separately, from staff). Never name customers or quote tickets. "
        f"{GROUNDING_RULES}",
        data,
        PostIncidentDraft,
        check=_check,
    )
    return compose_customer_summary(draft, facts), compose_internal_summary(draft, facts)
