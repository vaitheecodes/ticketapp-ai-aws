"""Endpoint-level tests for the hand-off escalation fix, on AI chats.

Bug: the AI answered an invoice/plan question with "I don't have direct
access to billing or account details... contact our Sales or Billing
team" and the conversation was left as if resolved. The customer's "yes
please" (accepting the AI's own offer to connect them) was then treated
as a fresh request and answered with more self-service text instead of
reaching a human.

backend/handoff_signals.py + backend/chats.py: a limitation admission in
the AI's own answer escalates the chat (unassigned ticket with the
hand-off brief), and an affirmative reply to a hand-off offer escalates
directly without re-classification.

Real temp SQLite DB via FastAPI's TestClient; the pipeline is a stub
that records invocations, and the AI hand-off brief is stubbed so the
queue contents can be asserted exactly. No live LLM calls.
"""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

from backend import db, graph, main, runtime, seed_data
from backend.ai_service import summarize_conversation
from backend.ai_service.schemas import HandoffSummary
from backend.conversation import ESCALATION_MARKER

_email_counter = itertools.count()

_LIMITATION_ANSWER = (
    "I don't have direct access to billing or account details. For invoice or "
    "plan specifics, please contact our Sales or Billing team. I can guide you "
    "on how to reach them, or connect you with support if you prefer."
)
_NORMAL_ANSWER = "To install the Site24x7 agent, download the installer for your OS..."
_BRIEF = HandoffSummary(
    issue="Customer asked about invoice/plan details; AI could not access billing.",
)


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
    monkeypatch.setattr(summarize_conversation, "summarize_escalation", lambda transcript, reason: _BRIEF)
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


def _technician_headers(client) -> dict:
    # Staff can't self-sign-up; use the dev-seeded demo technician.
    (tech,) = [p for p in seed_data.DEMO_STAFF if p["role"] == "technician"]
    res = client.post(
        "/api/auth/login", json={"email": tech["email"], "password": tech["password"]}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _start_chat(client, headers, stub_results, issue="can I see my invoice and plan"):
    stub = _StubGraph(stub_results)
    runtime.set_graph(stub)
    res = client.post("/api/chats", json={"issue": issue}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json(), stub


def _send(client, headers, session_id, message):
    res = client.post(f"/api/chats/{session_id}/messages", json={"message": message}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _ticket_row(ticket_id) -> dict:
    conn = db.get_connection()
    try:
        return dict(conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Limitation admission on the opening message
# ---------------------------------------------------------------------------
def test_limitation_answer_on_new_chat_escalates_instead_of_resolving(client):
    headers = _signup(client)
    chat, _ = _start_chat(client, headers, [_ai_result(_LIMITATION_ANSWER)])

    assert chat["status"] == "escalated"
    row = _ticket_row(chat["ticket_id"])
    assert row["status"] == "open"
    assert row["route"] == "human"
    assert row["assigned_technician_id"] is None
    # the AI's explanation is still shown so the customer sees why
    assert any(m["content"] == _LIMITATION_ANSWER for m in chat["messages"])


def test_escalated_chat_reaches_the_human_queue_with_a_summary(client):
    headers = _signup(client)
    chat, _ = _start_chat(client, headers, [_ai_result(_LIMITATION_ANSWER)])

    queue = client.get("/api/staff/tickets", headers=_technician_headers(client)).json()
    queued = [t for t in queue if t["ticket_id"] == chat["ticket_id"]]
    assert queued, f"ticket {chat['ticket_id']} missing from the human queue"
    # The queue row previews the hand-off brief, not the raw opening line.
    assert _BRIEF.issue in queued[0]["preview"]
    assert ESCALATION_MARKER in _ticket_row(chat["ticket_id"])["ticket_text"]


def test_normal_answer_keeps_chat_active(client):
    headers = _signup(client)
    chat, _ = _start_chat(
        client, headers, [_ai_result(_NORMAL_ANSWER, category="AGENT")],
        issue="how do I install the agent",
    )
    assert chat["status"] == "active"
    assert chat["ticket_id"] is None


# ---------------------------------------------------------------------------
# Limitation admission mid-chat
# ---------------------------------------------------------------------------
def test_limitation_answer_mid_chat_escalates(client, monkeypatch):
    headers = _signup(client)
    chat, stub = _start_chat(
        client, headers, [_ai_result(_NORMAL_ANSWER, category="AGENT")],
        issue="how do I install the agent",
    )
    monkeypatch.setattr(graph, "is_continuation_of_prior_response", lambda *a, **k: False)
    stub._results.append(_ai_result(_LIMITATION_ANSWER))

    after = _send(client, headers, chat["session_id"], "actually can you show my current plan cost")

    assert after["status"] == "escalated"
    assert any(m["content"] == _LIMITATION_ANSWER for m in after["messages"])
    row = _ticket_row(after["ticket_id"])
    assert row["status"] == "open"
    assert _BRIEF.issue in row["ticket_text"]


# ---------------------------------------------------------------------------
# Escalation acceptance ("yes please")
# ---------------------------------------------------------------------------
def test_yes_please_after_handoff_offer_escalates_without_reclassifying(client):
    headers = _signup(client)
    offer_answer = "Happy to help! Would you like me to connect you with our billing team?"
    chat, stub = _start_chat(client, headers, [_ai_result(offer_answer)])
    assert chat["status"] == "active"

    calls_before = len(stub.invocations)
    after = _send(client, headers, chat["session_id"], "yes please")

    assert len(stub.invocations) == calls_before
    assert after["status"] == "escalated"
    assert _ticket_row(after["ticket_id"])["status"] == "open"
    # the customer is told the ticket reference
    assert after["ticket_id"] in after["messages"][-1]["content"]


def test_thanks_after_handoff_offer_resolves_rather_than_escalating(client):
    headers = _signup(client)
    offer_answer = "Would you like me to connect you with our billing team?"
    chat, _ = _start_chat(client, headers, [_ai_result(offer_answer)])

    after = _send(client, headers, chat["session_id"], "thanks")
    assert after["status"] == "resolved"
    assert after["ticket_id"] is None


def test_affirmative_without_a_handoff_offer_is_not_an_escalation(client):
    headers = _signup(client)
    chat, _ = _start_chat(
        client, headers, [_ai_result(_NORMAL_ANSWER, category="AGENT")],
        issue="how do I install the agent",
    )
    after = _send(client, headers, chat["session_id"], "ok")
    # falls to the closing-ack gate instead
    assert after["status"] == "resolved"
