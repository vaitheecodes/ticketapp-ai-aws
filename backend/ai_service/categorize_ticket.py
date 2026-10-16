"""categorize_ticket: one of the 17 KB categories plus a confidence.

Raw model judgement only; the routing graph (backend/graph.py) combines it
with KB retrieval and applies the routing policy. An unknown category or
an out-of-range confidence fails validation instead of being guessed at.
"""

from __future__ import annotations

from backend.ai_service import provider
from backend.ai_service._format import candidates_text, history_text
from backend.ai_service.schemas import CATEGORIES, Categorization, CategorizeInput


def categorize_ticket(data: CategorizeInput) -> Categorization:
    return provider.complete_json(
        "You classify support tickets for Site24x7 (an infrastructure "
        f"monitoring product) into exactly one of these categories: {', '.join(CATEGORIES)}. "
        "Tickets are about monitor setup/troubleshooting, alerting, integrations, "
        "agent installation, licensing, or data retention — not e-commerce orders, "
        "shipping, or refunds. The kb_matches block lists the closest real KB "
        "entries; treat a close wording match there as strong evidence for that "
        "entry's category, even for a short or casually phrased ticket. "
        "confidence is how sure you are (0.0-1.0) that the category is right.",
        {
            "customer_history": history_text(data.history, with_status=True),
            "kb_matches": candidates_text(data.kb_candidates),
            "ticket": data.ticket_text,
        },
        Categorization,
    )
