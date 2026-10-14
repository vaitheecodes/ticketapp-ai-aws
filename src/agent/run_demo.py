"""Stage 1 checkpoint: run the LangGraph skeleton on a handful of dummy
tickets and print each one's routing trace.

No Bedrock calls, no real KB, no real classifier — the point of this
script is to show the graph shape and `decide_route` logic working
end-to-end before any of that gets wired in.

Run from the repo root:  python src/agent/run_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ on sys.path

from agent.graph import build_graph  # noqa: E402

DUMMY_TICKETS = [
    {
        "ticket_id": "TCK-DEMO-001",
        "channel": "chat",
        "customer_id": "CUST-000001",
        "instruction_text": "Site24x7 says Prod-WebServer-01 is down but the service is working",
        "language_tags": ["basic_syntax"],
    },
    {
        "ticket_id": "TCK-DEMO-002",
        "channel": "email",
        "customer_id": "CUST-000002",
        "instruction_text": "how do I configure an alert profile in Site24x7",
        "language_tags": ["basic_syntax", "politeness"],
    },
    {
        "ticket_id": "TCK-DEMO-003",
        "channel": "voice",
        "customer_id": "CUST-000003",
        "instruction_text": "your billing team keeps overcharging my license and this is unacceptable",
        "language_tags": ["offensive_language"],
    },
    {
        "ticket_id": "TCK-DEMO-004",
        "channel": "chat",
        "customer_id": "CUST-000004",
        "instruction_text": "something about how long you keep our monitoring data, not sure who to ask",
        "language_tags": ["colloquial"],
    },
    {
        "ticket_id": "TCK-DEMO-005",
        "channel": "email",
        "customer_id": "CUST-000005",
        "instruction_text": "we do not want our data retention policy changed without approval",
        "language_tags": ["negation"],
    },
    {
        "ticket_id": "TCK-DEMO-006",
        "channel": "chat",
        "customer_id": "CUST-000006",
        "instruction_text": "how do I install the Site24x7 agent on a Kubernetes cluster",
        "language_tags": ["basic_syntax"],
    },
]


def main() -> None:
    app = build_graph()

    for ticket in DUMMY_TICKETS:
        result = app.invoke(ticket)
        print(f"\n{'=' * 70}")
        print(f"ticket_id={result['ticket_id']}  text={result['instruction_text']!r}")
        print(
            f"  classified: intent={result['intent']} category={result['category']} "
            f"confidence={result['confidence']:.2f}"
        )
        print(f"  route: {result['route']}  ({result['route_reason']})")
        if result["route"] == "auto_resolve":
            print(f"  response: {result['response_text']}")
        else:
            print(f"  escalation_summary: {result['escalation_summary']}")
        print(f"  resolved_by={result['resolved_by']}  resolution_status={result['resolution_status']}")
        print(f"  trace: {' -> '.join(result['trace'])}")


if __name__ == "__main__":
    main()
