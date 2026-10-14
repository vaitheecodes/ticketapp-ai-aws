"""Endpoint-level tests for the hand-off escalation fix.

Bug: the AI answered an invoice/plan question with "I don't have direct
access to billing or account details... contact our Sales or Billing
team", and the ticket was left `resolved` — a confident classification
of a request the AI then admitted it couldn't fulfil. The customer's
"yes please" (accepting the AI's own offer to connect them) was then
treated as a fresh request and answered with *more* self-service
navigation text instead of creating a ticket for a human.

Fixed in backend/handoff_signals.py + backend/main.py: a limitation
admission in the AI's own answer escalates through the same
summarize_case/human_queue mechanism as any other escalation, and an
affirmative reply to a hand-off offer escalates directly without
re-classification.

Uses a real temp SQLite DB via FastAPI's TestClient, with
backend.main._graph and graph.summarize_case stubbed — no live LLM
calls, and the stub records invocations so tests can assert whether the
pipeline was re-run.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from backend import db, graph, main  # noqa: E402

_email_counter = itertools.count()

_LIMITATION_ANSWER = (
    "I don't have direct access to billing or account details. For invoice or "
    "plan specifics, please contact our Sales or Billing team. I can guide you "
    "on how to reach them, or connect you with support if you prefer."
)
_NORMAL_ANSWER = "To install the Site24x7 agent, download the installer for your OS..."
_CASE_SUMMARY = "Customer asked about invoice/plan details; AI could not access billing."


class _StubGraph:
    def __init__(self, results):
        self._results = list(results)
        self.invocations = []

    def invoke(self, state):
        self.invocations.append(state)
        return self._results.pop(0)


def _ai_result(ai_response, category="ACCOUNT", confidence=0.95):
    return {
        "route": "ai",
        "category": category,
        "confidence": confidence,
        "ai_response": ai_response,
        "clarification_attempts": 0,
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(graph, "summarize_case", lambda transcript: _CASE_SUMMARY)
    with TestClient(main.app) as c:
        yield c


def _signup(client) -> dict:
    email = f"handoff-{next(_email_counter)}@example.com"
    res = client.post(
        "/api/auth/customer/signup",
        json={"name": "Test Customer", "email": email, "password": "pw123456"},
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _agent_headers(client) -> dict:
    email = f"handoff-agent-{next(_email_counter)}@example.com"
    res = client.post(
        "/api/auth/agent/signup",
        json={"name": "Test Agent", "email": email, "password": "pw123456"},
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _raise_ticket(client, headers, stub_results, ticket_text="can I see my invoice and plan"):
    stub = _StubGraph(stub_results)
    main._graph = stub
    res = client.post(
        "/api/tickets/new", json={"ticket_text": ticket_text, "channel": "chat"}, headers=headers
    )
    assert res.status_code == 200, res.text
    return res.json(), stub


def _ticket_row(client, headers, ticket_id) -> dict:
    res = client.get("/api/tickets/mine", headers=headers)
    assert res.status_code == 200, res.text
    (row,) = [t for t in res.json() if t["ticket_id"] == ticket_id]
    return row


# ---------------------------------------------------------------------------
# Limitation admission on the opening message
# ---------------------------------------------------------------------------
def test_limitation_answer_on_new_ticket_escalates_instead_of_resolving(client):
    headers = _signup(client)
    ticket, _ = _raise_ticket(client, headers, [_ai_result(_LIMITATION_ANSWER)])
    ticket_id = ticket["ticket_id"]

    assert ticket["route"] == "human"
    row = _ticket_row(client, headers, ticket_id)
    assert row["status"] == "open"
    assert row["route"] == "human"


def test_escalated_limitation_ticket_reaches_the_human_queue_with_a_summary(client):
    """The actual deliverable from the bug report: it must show up in the
    agent queue with a case summary, not just as customer-facing text."""
    headers = _signup(client)
    ticket, _ = _raise_ticket(client, headers, [_ai_result(_LIMITATION_ANSWER)])
    ticket_id = ticket["ticket_id"]

    queue = client.get("/api/queue", headers=_agent_headers(client)).json()
    queued = [t for t in queue if t["ticket_id"] == ticket_id]
    assert queued, f"ticket {ticket_id} missing from the human queue"
    assert _CASE_SUMMARY in queued[0]["ticket_text"]
    assert main._ESCALATION_MARKER in queued[0]["ticket_text"]


def test_normal_answer_on_new_ticket_still_resolves(client):
    headers = _signup(client)
    ticket, _ = _raise_ticket(
        client, headers, [_ai_result(_NORMAL_ANSWER, category="AGENT")],
        ticket_text="how do I install the agent",
    )
    assert ticket["route"] == "ai"
    assert _ticket_row(client, headers, ticket["ticket_id"])["status"] == "resolved"


# ---------------------------------------------------------------------------
# Limitation admission mid-chat
# ---------------------------------------------------------------------------
def test_limitation_answer_mid_chat_escalates(client, monkeypatch):
    headers = _signup(client)
    ticket, stub = _raise_ticket(
        client, headers, [_ai_result(_NORMAL_ANSWER, category="AGENT")],
        ticket_text="how do I install the agent",
    )
    ticket_id = ticket["ticket_id"]

    # force the normal-pipeline path, and have it answer with a limitation
    monkeypatch.setattr(graph, "is_continuation_of_prior_response", lambda *a, **k: False)
    stub._results.append(_ai_result(_LIMITATION_ANSWER))

    chat = client.post(
        f"/api/tickets/{ticket_id}/chat",
        json={"message": "actually can you show my current plan cost"},
        headers=headers,
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["route"] == "human"
    # the AI's explanation is still returned so the customer sees why
    assert chat.json()["message"] == _LIMITATION_ANSWER

    row = _ticket_row(client, headers, ticket_id)
    assert row["route"] == "human"
    assert row["status"] == "open"
    assert _CASE_SUMMARY in row["ticket_text"]


# ---------------------------------------------------------------------------
# Escalation acceptance ("yes please")
# ---------------------------------------------------------------------------
def test_yes_please_after_handoff_offer_escalates_without_reclassifying(client):
    """The second half of the bug: agreeing to be connected must create a
    real queued ticket, not trigger another round of classification that
    generates more DIY instructions."""
    headers = _signup(client)
    # ticket resolves with an answer that *offers* a hand-off but doesn't
    # admit a limitation, so it stays resolved and the chat stays open
    offer_answer = "Happy to help! Would you like me to connect you with our billing team?"
    ticket, stub = _raise_ticket(client, headers, [_ai_result(offer_answer)])
    ticket_id = ticket["ticket_id"]
    assert ticket["route"] == "ai"

    calls_before = len(stub.invocations)
    chat = client.post(
        f"/api/tickets/{ticket_id}/chat", json={"message": "yes please"}, headers=headers
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["route"] == "human"

    # no fresh classification happened for the acceptance
    assert len(stub.invocations) == calls_before

    row = _ticket_row(client, headers, ticket_id)
    assert row["route"] == "human"
    assert row["status"] == "open"
    assert _CASE_SUMMARY in row["ticket_text"]

    messages = client.get(f"/api/tickets/{ticket_id}/messages", headers=headers).json()
    assert messages[-2]["message"] == "yes please"
    assert messages[-1]["sender"] == "system"


def test_thanks_after_handoff_offer_still_closes_rather_than_escalating(client):
    """Bare gratitude after an offer is a close, not an acceptance — the
    closing-ack gate must still own that case."""
    headers = _signup(client)
    offer_answer = "Would you like me to connect you with our billing team?"
    ticket, _ = _raise_ticket(client, headers, [_ai_result(offer_answer)])
    ticket_id = ticket["ticket_id"]

    chat = client.post(
        f"/api/tickets/{ticket_id}/chat", json={"message": "thanks"}, headers=headers
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["route"] == "closed"
    assert _ticket_row(client, headers, ticket_id)["status"] == "closed"


def test_affirmative_without_a_handoff_offer_is_not_an_escalation(client, monkeypatch):
    """"ok" after an ordinary answer must not escalate — the acceptance
    gate requires an actual offer in the prior reply."""
    headers = _signup(client)
    ticket, stub = _raise_ticket(
        client, headers, [_ai_result(_NORMAL_ANSWER, category="AGENT")],
        ticket_text="how do I install the agent",
    )
    ticket_id = ticket["ticket_id"]

    chat = client.post(
        f"/api/tickets/{ticket_id}/chat", json={"message": "ok"}, headers=headers
    )
    assert chat.status_code == 200, chat.text
    # falls to the closing-ack gate instead
    assert chat.json()["route"] == "closed"
