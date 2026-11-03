"""KB entries with handling "escalate_with_info" (data/kb/new_entries.json).

- handling and collect reach the retrieval index and the candidates.
- The reply gives numbered steps from the approved answer (the model's
  rewrite, or the answer's own lines when the rewrite asks a question or
  has no numbered steps), then the collect list and a fixed safety note;
  it never asks a question.
- The ticket is raised in the same turn: chat (escalated session +
  unassigned ticket), and a new email-style ticket (routed to a human).
- KB-N0140 (the HAR entry) grounds "invalid data provided for api" and
  stays unconfirmed when the customer hasn't mentioned an API.

Real temporary SQLite DB via FastAPI's TestClient; the pipeline is a stub
and the provider is disabled by tests/conftest.py unless a test stubs it.
"""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

from backend import db, graph, kb_new_entries, kb_retrieval, main, runtime, topic_terms
from backend.ai_service import chat_response, provider, summarize_ticket
from backend.ai_service.schemas import AnswerInput, HandoffSummary, KBCandidate

_counter = itertools.count()

ANSWER = ("Support may ask for a HAR file. Right-click the page and choose Inspect.\n"
          "Chrome or Edge: turn on \"Preserve log\", then choose \"Save all as HAR with content\".\n"
          "A HAR file can contain cookies and tokens, so attach it only to your ticket.")
ENTRY = KBCandidate(kb_entry_id="KB-N0901", category="WEB_MONITORING", intent="add_api_monitor_error_har",
                    instruction="invalid data provided error for an API monitor", response=ANSWER,
                    score=0.9, handling="escalate_with_info",
                    collect=["Exact error text", "A HAR file of the failing attempt"])
STEPS = ("A HAR file helps our team see what failed.\n"
         "1. Right-click the page and choose Inspect.\n"
         "2. In Chrome or Edge, turn on \"Preserve log\" in the Network tab.\n"
         "3. Choose \"Save all as HAR with content\".")


def _data(**kwargs):
    return AnswerInput(ticket_text="Customer: invalid data provided for api", kb_candidates=[ENTRY],
                       latest_message="invalid data provided for api", **kwargs)


def _model(monkeypatch, *replies):
    calls = []

    def fake(instructions, blocks, operation="", **kwargs):
        calls.append((operation, blocks))
        reply = replies[min(len(calls), len(replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(provider, "complete_text", fake)
    return calls


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def test_handling_and_collect_reach_the_index():
    data = {"entries": [{
        "kb_entry_id": "KB-N0901", "status": "approved", "reviewed_at": "2026-11-03T00:00:00+00:00",
        "area": "Add-monitor errors", "category": "WEB_MONITORING", "intent": "zebra_errors",
        "handling": "escalate_with_info", "collect": ["Exact error text"],
        "questions": ["zebra add error one", "zebra add error two", "zebra add error three"],
        "answer": ANSWER, "source_url": "https://www.site24x7.com/help/zebra.html"}]}
    report = kb_new_entries.validate(data, [])
    assert report.errors == []
    assert {(e["handling"], tuple(e["collect"])) for e in report.entries} == {
        ("escalate_with_info", ("Exact error text",))}
    (hit,) = graph._kb_candidates_to_dicts(kb_retrieval.build_index(report.entries).search("zebra add error one"))
    assert hit["handling"] == "escalate_with_info" and hit["collect"] == ["Exact error text"]


def test_base_kb_entries_default_to_answer():
    (hit, *_) = kb_retrieval.retrieve_candidates("how do I install the agent")
    assert hit.entry.handling == "answer" and hit.entry.collect == ()


# ---------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------
def test_numbered_steps_accepts_steps_and_drops_sensitive_sentences():
    assert chat_response.numbered_steps(STEPS) == STEPS
    with_note = STEPS + "\nNever share cookies or tokens."
    assert chat_response.numbered_steps(with_note) == STEPS
    assert chat_response.numbered_steps("Can you send a HAR file?\n1. a\n2. b") is None
    assert chat_response.numbered_steps("Just one paragraph, no steps.") is None


def test_steps_from_answer_numbers_the_answer_lines():
    steps = chat_response.steps_from_answer(ANSWER)
    assert steps.splitlines() == [
        "1. Support may ask for a HAR file. Right-click the page and choose Inspect.",
        "2. Chrome or Edge: turn on \"Preserve log\", then choose \"Save all as HAR with content\"."]


def test_info_request_gives_steps_collect_list_and_safety_note(monkeypatch):
    calls = _model(monkeypatch, "Hi there,\n" + STEPS)
    text = chat_response.info_request(_data()).text
    assert text.startswith("A HAR file helps") and "1. Right-click" in text
    assert "Please add these to your ticket:\n- Exact error text\n- A HAR file of the failing attempt" in text
    assert text.endswith(chat_response.INFO_SAFETY_NOTE)
    assert "?" not in text
    assert calls[0][0] == "info_request" and "Canonical guidance" in calls[0][1]["kb_guidance"]


@pytest.mark.parametrize("reply", [
    "Can you tell me which browser you use?",
    "Open developer tools and save the network log as a HAR file.",
    provider.AIError("empty", provider.AIFailure(kind="filter_rejected", operation="chat_filter")),
])
def test_info_request_falls_back_to_the_answer_lines(monkeypatch, reply):
    _model(monkeypatch, reply)
    text = chat_response.info_request(_data()).text
    assert text.startswith(chat_response.steps_from_answer(ANSWER))
    assert "?" not in text and text.endswith(chat_response.INFO_SAFETY_NOTE)


def test_info_request_provider_errors_still_raise(monkeypatch):
    _model(monkeypatch, provider.AIError("throttled", provider.AIFailure(kind="throttling", operation="x")))
    with pytest.raises(provider.AIError):
        chat_response.info_request(_data())


def test_info_request_email_format(monkeypatch):
    _model(monkeypatch, STEPS)
    text = chat_response.info_request(_data(reply_format="email", customer_name="Dana Smith")).text
    assert text.startswith("Hi Dana,") and "1. Right-click" in text and chat_response.INFO_SAFETY_NOTE in text


def test_generate_response_flags_needs_info_only_for_escalate_with_info(monkeypatch):
    _model(monkeypatch, STEPS)
    monkeypatch.setattr(chat_response, "answer", lambda data: chat_response.ChatReply(text="An answer."))
    state = {"ticket_text": "Customer: invalid data provided for api",
             "kb_candidates": [ENTRY.model_dump()]}
    result = graph.generate_response(state)
    assert result["needs_info"] == {"kb_entry_id": "KB-N0901", "collect": ENTRY.collect}
    assert "1. Right-click" in result["ai_response"]
    state["kb_candidates"] = [{**ENTRY.model_dump(), "handling": "answer"}]
    assert graph.generate_response(state) == {"ai_response": "An answer.", "needs_info": None}


# ---------------------------------------------------------------------------
# The ticket is raised in the same turn
# ---------------------------------------------------------------------------
class _StubGraph:
    def __init__(self, results):
        self._results = list(results)

    def invoke(self, state):
        return self._results.pop(0)


_CLARIFY = {"route": "clarify", "category": "WEB_MONITORING", "confidence": 0.5,
            "ai_response": "What type of monitor is it?", "clarification_attempts": 1}
_NEEDS_INFO = {"route": "ai", "category": "WEB_MONITORING", "confidence": 0.9, "clarification_attempts": 1,
               "ai_response": STEPS + "\n\n" + chat_response.INFO_SAFETY_NOTE,
               "needs_info": {"kb_entry_id": "KB-N0140", "collect": ["Exact error text"]}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(summarize_ticket, "summarize_ticket",
                        lambda thread, reason: HandoffSummary(issue="CASE SUMMARY"))
    with TestClient(main.app) as c:
        yield c


def _customer(client):
    res = client.post("/api/auth/customer/signup", json={
        "name": "Dana Smith", "email": f"info-{next(_counter)}@example.com", "password": "password123"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _query(sql, params=()):
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def test_chat_gives_the_steps_and_raises_the_ticket_in_the_same_turn(client):
    headers = _customer(client)
    runtime.set_graph(_StubGraph([_CLARIFY, _NEEDS_INFO]))
    chat = client.post("/api/chats", json={"issue": "i am facing issue in adding monitor"}, headers=headers).json()
    assert chat["status"] == "active"
    res = client.post(f"/api/chats/{chat['session_id']}/messages",
                      json={"message": "invalid data provided for api"}, headers=headers).json()
    assert res["status"] == "escalated" and res["ticket_id"]
    steps_msg, ticket_msg = res["messages"][-2:]
    assert steps_msg["role"] == "ai" and "1. Right-click" in steps_msg["content"]
    assert chat_response.INFO_SAFETY_NOTE in steps_msg["content"] and "?" not in steps_msg["content"]
    assert res["ticket_id"] in ticket_msg["content"]
    (ticket,) = _query("SELECT * FROM tickets WHERE ticket_id = ?", (res["ticket_id"],))
    assert (ticket["status"], ticket["route"], ticket["assigned_technician_id"]) == ("open", "human", None)
    assert "support needs details from the customer" in ticket["ticket_text"]


def test_new_ticket_with_needs_info_goes_to_the_team(client):
    headers = _customer(client)
    runtime.set_graph(_StubGraph([{**_NEEDS_INFO, "clarification_attempts": 0}]))
    res = client.post("/api/tickets", json={"subject": "API monitor error", "priority": "high",
                                            "description": "invalid data provided for api"}, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["route"], body["status"]) == ("human", "open")
    (ticket,) = _query("SELECT * FROM tickets WHERE ticket_id = ?", (body["ticket_id"],))
    assert ticket["route"] == "human" and ticket["assigned_technician_id"] is None


# ---------------------------------------------------------------------------
# KB-N0140 on the real KB
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text, usable", [
    ("invalid data provided for api", True),
    ("my REST API monitor says invalid data provided", True),
    ("monitor will not save", False),
    ("error when adding a monitor", False),
])
def test_the_har_entry_grounds_only_once_an_api_is_mentioned(text, usable):
    kb_retrieval._load_index.cache_clear()
    topic_terms.reload()
    try:
        state = {"ticket_text": f"Customer: {text}", "kb_candidates": graph.kb_candidates_for(text)}
        ok, unconfirmed = graph.grounding_candidates(state)
        ids = [c["kb_entry_id"] for c in ok] if usable else [c["kb_entry_id"] for c, _ in unconfirmed]
        assert "KB-N0140" in ids
        if usable:
            assert ok[0]["kb_entry_id"] == "KB-N0140" and ok[0]["handling"] == "escalate_with_info"
    finally:
        kb_retrieval._load_index.cache_clear()
        topic_terms.reload()


def test_for_api_counts_as_an_api_mention_but_an_api_key_does_not():
    assert "API" in topic_terms.mentioned("invalid data provided for api")
    assert "API" not in topic_terms.mentioned("I lost my API key")
