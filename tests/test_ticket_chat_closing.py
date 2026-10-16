"""Integration tests for backend/main.py's POST /api/tickets/{id}/chat
pre-pipeline shortcuts: the "thanks" closing-acknowledgment fix and the
"what's the difference" continuation fix.

Bug 1 (closing ack): every chat message re-invoked the full
classify_ticket/decide_route pipeline with no distinction between
"customer is still describing their issue" and "customer is just
acknowledging the resolution is done" — a bare "thanks" on an
already-resolved ticket got classified with near-zero confidence and
wrongly escalated to a human. Fixed by backend/closing_ack.py: a bare
closing acknowledgment on a `status == "resolved"` ticket short-circuits
the pipeline, stores the message, and locks the ticket at
`status == "closed"`.

Bug 2 (continuation): a genuine on-topic follow-up ("what is the
difference" after the AI just explained two things to compare) also got
classified in relative isolation, collapsed confidence, and wrongly
escalated — even though the full conversation makes it clearly
answerable. Fixed by graph.is_continuation_of_prior_response: a message
that refers back to the AI's own last reply skips classify_ticket/
decide_route and answers directly via generate_response. A message with
escalation language (negation/offensive/coordinated) must still hit the
hard override in decide_route even if it's topically a continuation
("thanks, but it's still not working"), so graph.has_escalation_language
gates the continuation shortcut before it's ever considered.

Uses a real (temporary, isolated) SQLite DB via FastAPI's TestClient.
backend.main._graph is swapped for a stub that returns pre-programmed
results and records every invocation, so these tests need no live LLM
call for the classify_ticket/decide_route path and can assert exactly
when the pipeline was, or wasn't, re-run. graph.is_continuation_of_prior_response
and graph.generate_response are likewise monkeypatched where a test
needs to control or observe the continuation path deterministically.
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


class _StubGraph:
    """Replaces the real LangGraph app: returns pre-programmed results in
    order, and records every invocation so tests can assert whether the
    pipeline was re-run."""

    def __init__(self, results):
        self._results = list(results)
        self.invocations = []

    def invoke(self, state):
        self.invocations.append(state)
        return self._results.pop(0)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with TestClient(main.app) as c:
        yield c


def _signup(client) -> dict:
    email = f"closing-ack-{next(_email_counter)}@example.com"
    res = client.post(
        "/api/auth/customer/signup",
        json={"name": "Test Customer", "email": email, "password": "pw123456"},
    )
    assert res.status_code == 200, res.text
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _raise_ticket(client, headers, stub_result, ticket_text="how do I install the Site24x7 agent"):
    stub = _StubGraph([stub_result])
    main._graph = stub
    res = client.post(
        "/api/tickets/new",
        json={"ticket_text": ticket_text, "channel": "chat"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json(), stub


def _ticket_status(client, headers, ticket_id) -> str:
    res = client.get(f"/api/tickets/{ticket_id}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["ticket"]["status"]


_RESOLVED_RESULT = {
    "route": "ai",
    "category": "AGENT",
    "confidence": 0.95,
    "ai_response": "Here's how to install the Site24x7 agent...",
    "clarification_attempts": 0,
}


def test_bare_thanks_on_resolved_ticket_closes_without_reinvoking_pipeline(client):
    headers = _signup(client)
    ticket, stub = _raise_ticket(client, headers, _RESOLVED_RESULT)
    ticket_id = ticket["ticket_id"]
    assert ticket["route"] == "ai"
    assert _ticket_status(client, headers, ticket_id) == "resolved"

    calls_before = len(stub.invocations)
    chat = client.post(
        f"/api/tickets/{ticket_id}/chat", json={"message": "thanks"}, headers=headers
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["route"] == "closed"

    # the pipeline must NOT have been re-invoked for a bare "thanks"
    assert len(stub.invocations) == calls_before
    assert _ticket_status(client, headers, ticket_id) == "closed"

    # the closed ticket's message log should contain the customer's
    # "thanks" and a system close notice, not another AI/escalation reply
    messages = client.get(f"/api/tickets/{ticket_id}/messages", headers=headers).json()
    assert messages[-2]["sender"] == "customer"
    assert messages[-2]["message"] == "thanks"
    assert messages[-1]["sender"] == "system"
    assert "resolved" in messages[-1]["message"].lower()


def test_closed_ticket_rejects_further_messages(client):
    headers = _signup(client)
    ticket, _ = _raise_ticket(client, headers, _RESOLVED_RESULT)
    ticket_id = ticket["ticket_id"]

    first = client.post(
        f"/api/tickets/{ticket_id}/chat", json={"message": "thanks"}, headers=headers
    )
    assert first.json()["route"] == "closed"

    second = client.post(
        f"/api/tickets/{ticket_id}/chat", json={"message": "hello again"}, headers=headers
    )
    assert second.status_code == 400


def test_substantive_followup_after_resolution_still_reruns_pipeline(client):
    """"thanks, but it's still not working" is not a bare acknowledgment
    (continuation signal "but"/"still"/"not") -- it must fall through to
    the normal pipeline and can re-open the ticket, exactly like any
    other follow-up message would have before this fix."""
    headers = _signup(client)
    ticket, stub = _raise_ticket(client, headers, _RESOLVED_RESULT)
    ticket_id = ticket["ticket_id"]
    assert _ticket_status(client, headers, ticket_id) == "resolved"

    # program the second (re-)invocation result: still unresolved, so
    # decide_route would realistically escalate -- proves the pipeline
    # genuinely re-ran rather than being silently swallowed.
    stub._results.append(
        {
            "route": "human",
            "category": "AGENT",
            "confidence": 0.3,
            "ai_response": None,
            "clarification_attempts": 0,
        }
    )

    calls_before = len(stub.invocations)
    chat = client.post(
        f"/api/tickets/{ticket_id}/chat",
        json={"message": "thanks, but it's still not working"},
        headers=headers,
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["route"] == "human"

    # the pipeline WAS re-invoked this time
    assert len(stub.invocations) == calls_before + 1
    assert _ticket_status(client, headers, ticket_id) == "open"


def test_question_after_resolution_never_treated_as_closing_ack(client, monkeypatch):
    """A follow-up question (contains "?") must never be treated as a
    closing acknowledgment, even if short -- regardless of which path
    (continuation or normal pipeline) it ultimately takes."""
    headers = _signup(client)
    ticket, stub = _raise_ticket(client, headers, _RESOLVED_RESULT)
    ticket_id = ticket["ticket_id"]

    # Force the "genuinely new topic" branch so this test only exercises
    # the closing-ack-vs-not distinction, not the continuation check.
    monkeypatch.setattr(graph, "is_continuation_of_prior_response", lambda *a, **k: False)
    stub._results.append(
        {
            "route": "ai",
            "category": "AGENT",
            "confidence": 0.9,
            "ai_response": "Sure, here's more detail...",
            "clarification_attempts": 0,
        }
    )
    calls_before = len(stub.invocations)
    chat = client.post(
        f"/api/tickets/{ticket_id}/chat",
        json={"message": "what about Windows?"},
        headers=headers,
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["route"] == "ai"
    assert len(stub.invocations) == calls_before + 1
    assert _ticket_status(client, headers, ticket_id) == "resolved"


def test_continuation_question_answers_directly_without_reinvoking_pipeline(client, monkeypatch):
    """The bug-report scenario: a follow-up that refers back to the AI's
    own last reply ("what is the difference") must be answered directly
    -- classify_ticket/decide_route (the stub graph) must NOT be
    re-invoked at all for this turn."""
    headers = _signup(client)
    ticket, stub = _raise_ticket(client, headers, _RESOLVED_RESULT)
    ticket_id = ticket["ticket_id"]

    monkeypatch.setattr(graph, "is_continuation_of_prior_response", lambda *a, **k: True)
    monkeypatch.setattr(
        graph, "generate_response", lambda state: {"ai_response": "Here's the difference..."}
    )

    calls_before = len(stub.invocations)
    chat = client.post(
        f"/api/tickets/{ticket_id}/chat",
        json={"message": "what is the differnce"},
        headers=headers,
    )
    assert chat.status_code == 200, chat.text
    assert chat.json() == {"route": "ai", "message": "Here's the difference..."}

    # the stub graph (classify_ticket/decide_route) must NOT have been
    # re-invoked -- this turn was answered via the continuation path
    assert len(stub.invocations) == calls_before
    assert _ticket_status(client, headers, ticket_id) == "resolved"

    messages = client.get(f"/api/tickets/{ticket_id}/messages", headers=headers).json()
    assert messages[-2]["sender"] == "customer"
    assert messages[-2]["message"] == "what is the differnce"
    assert messages[-1]["sender"] == "ai"
    assert messages[-1]["message"] == "Here's the difference..."


def test_continuation_generate_response_told_it_is_a_continuation(client, monkeypatch):
    """generate_response must be called with continuation=True so its
    prompt treats the transcript as an ongoing exchange to build on
    (see graph.generate_response's continuation_note)."""
    headers = _signup(client)
    ticket, stub = _raise_ticket(client, headers, _RESOLVED_RESULT)
    ticket_id = ticket["ticket_id"]

    captured_states = []

    def _fake_generate_response(state):
        captured_states.append(state)
        return {"ai_response": "..."}

    monkeypatch.setattr(graph, "is_continuation_of_prior_response", lambda *a, **k: True)
    monkeypatch.setattr(graph, "generate_response", _fake_generate_response)

    client.post(
        f"/api/tickets/{ticket_id}/chat",
        json={"message": "what is the differnce"},
        headers=headers,
    )
    assert len(captured_states) == 1
    assert captured_states[0]["continuation"] is True
    assert captured_states[0]["category"] == _RESOLVED_RESULT["category"]


def test_escalation_language_blocks_continuation_shortcut(client, monkeypatch):
    """"thanks, but it's still not working" is topically a continuation
    of the same thread, but the negation must still trip the hard
    escalation override in decide_route -- is_continuation_of_prior_response
    must never even be consulted for a message like this."""
    headers = _signup(client)
    ticket, stub = _raise_ticket(client, headers, _RESOLVED_RESULT)
    ticket_id = ticket["ticket_id"]

    def _fail_if_called(*args, **kwargs):
        raise AssertionError(
            "is_continuation_of_prior_response must not be called when "
            "has_escalation_language(message) is True"
        )

    monkeypatch.setattr(graph, "is_continuation_of_prior_response", _fail_if_called)
    stub._results.append(
        {
            "route": "human",
            "category": "AGENT",
            "confidence": 0.3,
            "ai_response": None,
            "clarification_attempts": 0,
        }
    )

    calls_before = len(stub.invocations)
    chat = client.post(
        f"/api/tickets/{ticket_id}/chat",
        json={"message": "thanks, but it's still not working"},
        headers=headers,
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["route"] == "human"
    assert len(stub.invocations) == calls_before + 1
    assert _ticket_status(client, headers, ticket_id) == "open"


def test_genuinely_new_topic_after_resolution_reruns_pipeline(client, monkeypatch):
    """A customer pivoting to an unrelated request mid-chat must still
    be classified fresh, not force-answered as a continuation."""
    headers = _signup(client)
    ticket, stub = _raise_ticket(client, headers, _RESOLVED_RESULT)
    ticket_id = ticket["ticket_id"]

    monkeypatch.setattr(graph, "is_continuation_of_prior_response", lambda *a, **k: False)
    stub._results.append(
        {
            "route": "ai",
            "category": "AGENT",
            "confidence": 0.9,
            "ai_response": "To install the agent on Ubuntu...",
            "clarification_attempts": 0,
        }
    )

    calls_before = len(stub.invocations)
    chat = client.post(
        f"/api/tickets/{ticket_id}/chat",
        json={"message": "separately, how do I install the agent on Ubuntu"},
        headers=headers,
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["route"] == "ai"
    assert len(stub.invocations) == calls_before + 1
    assert _ticket_status(client, headers, ticket_id) == "resolved"
