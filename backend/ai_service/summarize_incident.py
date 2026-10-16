"""summarize_incident: refresh an incident's summary when tickets join it.
States only what the linked tickets show; no root-cause claims."""

from __future__ import annotations

from backend.ai_service import provider
from backend.ai_service._format import GROUNDING_RULES
from backend.ai_service.schemas import IncidentSummary, SummarizeIncidentInput


def summarize_incident(data: SummarizeIncidentInput) -> IncidentSummary:
    tickets = "\n\n".join(
        f"ticket_id: {t.ticket_id}\ncustomer: {t.customer_ref}\nreported_at: {t.created_at}\n"
        f"subject: {t.subject}\ndetails: {t.details}"
        for t in data.tickets
    )
    return provider.complete_json(
        "Write a concise summary of a possible Site24x7 incident for the support "
        "manager reviewing it: a short neutral title, a 2-4 sentence summary of "
        "what customers are reporting, the common symptoms, the affected services "
        "or components, and investigative next steps. Do not state a root cause "
        f"or call it a confirmed outage. {GROUNDING_RULES}",
        {
            "current_title": data.title,
            "observed_facts": "\n".join(f"- {f}" for f in data.observed_facts) or "(none)",
            "hypotheses": "\n".join(f"- {h}" for h in data.hypotheses) or "(none)",
            "tickets": tickets,
        },
        IncidentSummary,
    )
