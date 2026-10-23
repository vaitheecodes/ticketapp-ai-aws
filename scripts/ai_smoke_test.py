"""Run the 10 most important AI prompts against the configured provider
and report invalid JSON / weak answers. Not a unit test: it calls the
real model (AI_PROVIDER and its credentials from .env).

    AI_PROVIDER=quickml_rest .ven/bin/python scripts/ai_smoke_test.py

Prints, per prompt: OK/FAIL, seconds, attempts (a retry means the first
reply was rejected), and the parsed output. Never prints credentials.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import graph  # noqa: E402  (also loads .env via backend/__init__)
from backend.ai_service import (  # noqa: E402
    AIError, categorize_ticket, chat_response, correlate_incidents, draft_post_incident,
    incident_question, provider, suggest_steps, summarize_conversation, summarize_incident,
)
from backend.ai_service.schemas import (  # noqa: E402
    AnswerInput, CategorizeInput, ClarifyInput, ContinuationInput, CorrelateInput, CorrelationTicket,
    DepartmentOption, IncidentQuestionInput, KBCandidate, PostIncidentFacts, SuggestStepsInput,
    SummarizeIncidentInput,
)


class _Retries(logging.Handler):
    count = 0

    def emit(self, record):
        if "failed" in record.getMessage() and "validation" in record.getMessage():
            _Retries.count += 1


def kb(text):
    return [KBCandidate(**c) for c in graph.kb_candidates_for(text)]


AGENT_DOWN = "My Linux server monitor shows no data since 10:00. The agent service seems stopped."
TRANSCRIPT = (
    "Customer: My Linux server monitor shows no data since 10:00.\n"
    "Agent (AI): Please restart the Site24x7 agent with `/etc/init.d/site24x7monagent restart`.\n"
    "Customer: I restarted it, still no data. I need a human please."
)
TICKETS = [
    CorrelationTicket(ticket_id=f"TCK-{i}", customer_ref=f"C{i}", created_at=f"2026-10-23T10:0{i}:00",
                      category=cat, subject=subj, details=det)
    for i, (cat, subj, det) in enumerate([
        ("SERVER_MONITORING", "No data from server monitors", "All Linux servers show no data since 10:00 IST."),
        ("AGENT", "Agent not reporting", "Windows agent stopped reporting around 10:05, restarted, no change."),
        ("SERVER_MONITORING", "Monitors in unknown state", "Server monitors went grey (unknown) at 10:02."),
    ])
]
DEPTS = [DepartmentOption(name="Infrastructure", categories=["SERVER_MONITORING", "AGENT"])]

CASES = [
    ("1 categorize (clear)", lambda: categorize_ticket.categorize_ticket(
        CategorizeInput(ticket_text=AGENT_DOWN, kb_candidates=kb(AGENT_DOWN)))),
    ("2 categorize (billing, high-risk)", lambda: categorize_ticket.categorize_ticket(
        CategorizeInput(ticket_text="Please downgrade my plan and refund last month's invoice."))),
    ("3 chat answer", lambda: chat_response.answer(AnswerInput(
        ticket_text="How do I set up an alert when CPU goes above 90%?",
        kb_candidates=kb("alert CPU threshold"), customer_name="Asha"))),
    ("4 clarifying question", lambda: chat_response.clarify(ClarifyInput(
        ticket_text="it is not working", kb_candidates=kb("monitor not working")))),
    ("5 is_continuation", lambda: chat_response.is_continuation(ContinuationInput(
        message="what's the difference between the two?",
        prior_ai_response="You can use a Website monitor or a REST API monitor for this."))),
    ("6 escalation summary", lambda: summarize_conversation.summarize_escalation(
        TRANSCRIPT, "The customer asked for a human agent.")),
    ("7 suggested steps", lambda: suggest_steps.suggest_steps(SuggestStepsInput(
        summary=summarize_conversation.fallback_handoff(AGENT_DOWN, "customer request"),
        category="AGENT", kb_candidates=kb(AGENT_DOWN)))),
    ("8 correlate incidents", lambda: correlate_incidents.correlate_incidents(
        CorrelateInput(tickets=TICKETS, departments=DEPTS, window_hours=12))),
    ("9 incident summary", lambda: summarize_incident.summarize_incident(SummarizeIncidentInput(
        title="Server monitors not collecting data", tickets=TICKETS,
        observed_facts=["3 customers report no data from 10:00"], hypotheses=["Collector issue"]))),
    ("10 post-incident draft", lambda: draft_post_incident.draft_post_incident(PostIncidentFacts(
        title="Server monitors not collecting data", category="SERVER_MONITORING",
        started_at_ist="23 Oct 2026, 10:00 IST", resolved_at_ist="23 Oct 2026, 11:15 IST",
        common_symptoms=["No data from server monitors"], affected_services=["Server monitoring"],
        observed_facts=["3 customers reported no data"], resolution_note="Restarted the stalled collectors.",
        root_cause=None, affected_customer_count=3, affected_ticket_count=3))),
    ("11 incident question", lambda: incident_question.parse_incident_question(IncidentQuestionInput(
        message="were you down yesterday around 3pm?", now_ist="2026-10-23T12:00:00+05:30",
        memory_days=90))),
]


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="  log: %(message)s")
    logging.getLogger().addHandler(_Retries())
    print(f"provider: {provider.provider_name()}\n")
    failures = 0
    for name, run in CASES:
        _Retries.count = 0
        start = time.time()
        try:
            out = run()
            status = "OK"
        except AIError as exc:
            out, status = str(exc), "FAIL"
            failures += 1
        elapsed = time.time() - start
        retry = f", {_Retries.count} rejected repl{'y' if _Retries.count == 1 else 'ies'}" if _Retries.count else ""
        print(f"[{status}] {name} ({elapsed:.1f}s{retry})")
        if hasattr(out, "model_dump"):
            out = out.model_dump()
        print("   " + (json.dumps(out, indent=2, default=str) if not isinstance(out, str) else out)
              .replace("\n", "\n   ")[:2500] + "\n")
    print(f"{len(CASES) - failures}/{len(CASES)} succeeded")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
