"""correlate_incidents: judge whether a candidate group of tickets (from
different customers, within a time window) plausibly share one incident.

Similar wording alone is not evidence: the model is told to weigh time
proximity, common symptoms, affected components and technical details,
to keep observed facts apart from hypotheses, and to say "Requires
further investigation" unless the tickets support a shared cause. The
output is checked against the input: related ticket ids must be from the
group and departments from the offered list, else it's invalid output
(retried once, then AIError). Customers appear only as pseudonymous refs.
"""

from __future__ import annotations

from backend.ai_service import provider
from backend.ai_service.schemas import CorrelateInput, CorrelationJudgement


def _render(data: CorrelateInput) -> dict[str, str]:
    tickets = "\n\n".join(
        f"ticket_id: {t.ticket_id}\ncustomer: {t.customer_ref}\nreported_at: {t.created_at}\n"
        f"category: {t.category or 'unknown'}\nsubject: {t.subject}\ndetails: {t.details}"
        for t in data.tickets
    )
    departments = "\n".join(
        f"- {d.name}: {', '.join(d.categories) or '(no categories)'}" for d in data.departments
    ) or "(none configured)"
    return {"time_window_hours": str(data.window_hours), "departments": departments, "tickets": tickets}


def correlate_incidents(data: CorrelateInput) -> CorrelationJudgement:
    ticket_ids = {t.ticket_id for t in data.tickets}
    department_names = {d.name for d in data.departments}

    def check(result: CorrelationJudgement) -> None:
        if not set(result.related_ticket_ids) <= ticket_ids:
            raise ValueError("related_ticket_ids not in the candidate group")
        if not set(result.candidate_departments) <= department_names:
            raise ValueError("candidate_departments not in the offered list")
        if result.is_related and len(result.related_ticket_ids) < 2:
            raise ValueError("a related group needs at least two tickets")

    return provider.complete_json(
        "You help a Site24x7 support manager spot incidents: several customers "
        "reporting what may be the same underlying problem. Judge whether the "
        "tickets in the tickets block (or a subset of them) plausibly share one "
        "incident. Similar wording alone is NOT evidence. Weigh time proximity, "
        "concrete common symptoms, the affected component or service, and any "
        "technical details (errors, regions, agent versions). Put only what the "
        "tickets state in observed_facts, and your interpretations in hypotheses. "
        "possible_shared_cause must be 'Requires further investigation' unless the "
        "tickets themselves support a specific cause. confidence (0-1) reflects "
        "how strongly the evidence links the tickets, with confidence_reasons. "
        "suggested_next_steps are investigative actions, not conclusions. "
        "candidate_departments may only use names from the departments block, and "
        "only when the affected component clearly belongs to them; leave it empty "
        "if unsure. If nothing links them, set is_related false.",
        _render(data),
        CorrelationJudgement,
        check=check,
    )
