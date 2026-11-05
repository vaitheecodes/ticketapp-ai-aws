"""How-to questions: one fixed question when a detail is missing, then an
answer from the KB or a ticket, never invented steps and never a loop.

- No kind of monitor named ("How to add the monitor"): ONE question
  offering the kinds (website, server/agent, APM, mobile app, database,
  cloud, network device), no ticket yet, no model call, never a monitor
  name or an account identifier. After the answer: the KB entry, or
  "I don't have a guide for that yet" and a ticket ("not sure" too).
- Subject clear and a KB how-to on it: answered from that entry, even with
  extra words ("in monitoring agent", "for uptime of last month"); a
  one-word overlap on something else ("an SLA report for my website" vs
  the website-check entry) doesn't count.
- Subject clear, no KB entry: the friendly reply and a ticket.
- Guides registered as specific to Linux / Windows (kb_registry.json) and
  neither named: "Linux or Windows?", then the matching guide.
- The ticket form (email replies) takes the same path.

Real FastAPI app, LangGraph pipeline, KB retrieval and SQLite; the how-to
drafts are approved in a temporary copy, and the language model is a
scripted fake.
"""

from __future__ import annotations

import itertools
import json

import pytest
from fastapi.testclient import TestClient

from backend import db, graph, kb_new_entries, kb_registry, kb_retrieval, main, runtime, topic_terms
from backend.ai_service import provider

_counter = itertools.count()
TYPE_QUESTION = graph.HOW_TO_TYPE_QUESTION.format(verb="add")
# No numbered steps: grounding.py would drop steps the KB entry lacks.
ANSWER = "Here is how to do that from the guide."


class _Model:
    def __init__(self):
        self.category, self.confidence = "MONITORING", 0.8
        self.calls: list[tuple[str, str]] = []  # (operation, user prompt)

    def __call__(self, messages, **kwargs):
        system, user = messages[0]["content"], messages[1]["content"]
        if "You classify support tickets" in system:
            self.calls.append(("categorize", user))
            return json.dumps({"category": self.category, "confidence": self.confidence})
        if "Site24x7 support assistant" in system or "helpful customer support agent" in system:
            self.calls.append(("answer", user))
            return ANSWER
        if "taking over" in system:
            self.calls.append(("handoff_summary", user))
            return json.dumps({"issue": "The customer wants to add a monitor.", "tried": [], "findings": [],
                               "unresolved_questions": []})
        if "Suggest 2-5 concrete next steps" in system:
            self.calls.append(("suggest_steps", user))
            return json.dumps({"steps": ["Check the guide."]})
        self.calls.append(("other", system[:60]))
        raise AssertionError(f"Unexpected AI operation: {system[:80]}")

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def answered_from(self) -> str:
        return next(user for op, user in self.calls if op == "answer")


@pytest.fixture(autouse=True)
def kb(tmp_path, monkeypatch):
    """The how-to drafts approved, and an empty registry, in temp files."""
    data = json.loads(kb_new_entries.NEW_ENTRIES_PATH.read_text(encoding="utf-8"))
    for entry in data["entries"]:
        if entry.get("area") == "Getting started (how-to)":
            entry["status"] = "approved"
    entries = tmp_path / "new_entries.json"
    entries.write_text(json.dumps(data), encoding="utf-8")
    registry = tmp_path / "kb_registry.json"
    registry.write_text(json.dumps(kb_registry.empty()), encoding="utf-8")
    monkeypatch.setattr(kb_new_entries, "NEW_ENTRIES_PATH", entries)
    monkeypatch.setattr(kb_registry, "REGISTRY_PATH", registry)
    kb_retrieval._load_index.cache_clear()
    topic_terms.reload()
    if kb_retrieval._load_index() is None:
        pytest.skip("data/kb/kb_entries.json not found")
    yield registry
    kb_retrieval._load_index.cache_clear()
    topic_terms.reload()


def _mark_agent_guides_os_specific(registry_path):
    registry = kb_registry.empty()
    registry["intent_terms"]["agent_installation"] = ["Linux", "Windows"]
    kb_registry.save(registry, registry_path)
    topic_terms.reload()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    model = _Model()
    monkeypatch.setattr(provider, "complete", model)
    with TestClient(main.app) as c:
        runtime.set_graph(graph.build_graph())
        c.model = model
        yield c


def _customer(client):
    res = client.post("/api/auth/customer/signup", json={
        "name": "Dana Smith", "email": f"howto-{next(_counter)}@example.com", "password": "password123"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _chat(client, headers, issue):
    res = client.post("/api/chats", json={"issue": issue}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _send(client, headers, chat, message):
    res = client.post(f"/api/chats/{chat['session_id']}/messages", json={"message": message}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _ai_lines(chat):
    return [m["content"] for m in chat["messages"] if m["role"] == "ai"]


def _usable(question: str) -> list[str]:
    text = f"Customer: {question}"
    state = {"ticket_text": text}
    state.update(graph.retrieve_kb_candidates(state))
    usable, _ = graph.grounding_candidates(state)
    return [c["kb_entry_id"] for c in usable]


# ---------------------------------------------------------------------------
# No kind of monitor named: one question, then the KB or a ticket
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("message, verb", [
    ("How to add the monitor", "add"),
    ("I want to add a new monitor", "add"),
    ("how do I set it up", "set up"),
    ("how to configure", "set up"),
])
def test_a_how_to_with_no_subject_gets_one_question_and_no_ticket(client, message, verb):
    headers = _customer(client)
    chat = _chat(client, headers, message)
    assert chat["status"] == "active" and not chat.get("ticket_id")
    assert _ai_lines(chat) == [graph.HOW_TO_TYPE_QUESTION.format(verb=verb)]
    assert "answer" not in client.model.ops()  # fixed text, no model call
    question = _ai_lines(chat)[0].lower()
    for kind in ("website", "server", "apm", "mobile app", "database", "cloud", "network device"):
        assert kind in question
    assert "name" not in question and "account" not in question and "id" not in question.split()


def test_the_answer_to_the_question_is_answered_from_the_kb(client):
    headers = _customer(client)
    chat = _chat(client, headers, "How to add the monitor")
    chat = _send(client, headers, chat, "a website")
    assert chat["status"] == "active"
    assert _ai_lines(chat) == [TYPE_QUESTION, ANSWER]
    assert "KB-N0143" in client.model.answered_from() or "website" in client.model.answered_from().lower()
    assert _ai_lines(chat).count(TYPE_QUESTION) == 1


def test_no_guide_after_the_question_raises_a_ticket(client):
    headers = _customer(client)
    chat = _chat(client, headers, "How to add the monitor")
    chat = _send(client, headers, chat, "a database")
    assert chat["status"] == "escalated" and chat["ticket_id"]
    lines = _ai_lines(chat)
    assert lines[0] == TYPE_QUESTION and lines[1] == graph.NO_KB_REPLY_CHAT
    assert chat["ticket_id"] in lines[2]
    assert "answer" not in client.model.ops()  # no improvised steps


def test_not_sure_after_the_question_raises_a_ticket_never_a_second_question(client):
    # "not" carries the negation tag, whose hand-off wins (routing policy).
    headers = _customer(client)
    chat = _chat(client, headers, "How to add the monitor")
    chat = _send(client, headers, chat, "not sure")
    assert chat["status"] == "escalated" and chat["ticket_id"]
    lines = _ai_lines(chat)
    assert lines[0] == TYPE_QUESTION and len(lines) == 2 and chat["ticket_id"] in lines[1]
    assert "answer" not in client.model.ops()


# ---------------------------------------------------------------------------
# Subject clear: the KB entry about that task, extra words or not
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("question, entries", [
    ("how to add a monitor in monitoring agent", {"KB-N0141", "KB-N0142"}),
    ("how can I add a new server monitor using the agent on my linux machine", {"KB-N0141"}),
    ("how do I generate a report for uptime of last month", {"KB-N0144"}),
    ("how to add a website to monitor my company homepage", {"KB-N0143"}),
    ("how to create a monitor group for my production servers", {"KB-N0149"}),
    ("I need to add a APM monitor, how to do?", {"KB-N0152"}),
    ("I need to add iOS app", {"KB-N0150"}),
])
def test_natural_wording_finds_the_how_to_entry(question, entries):
    assert _usable(question)[:1] and _usable(question)[0] in entries


@pytest.mark.parametrize("question, not_this", [
    ("how do I set up an SLA report for my website", "KB-N0143"),  # shares only "website"
    ("how do I add a dashboard widget for my website", "KB-N0143"),
])
def test_a_one_word_overlap_on_something_else_does_not_count(question, not_this):
    assert not_this not in _usable(question)


def test_a_clear_how_to_is_answered_from_its_entry(client):
    headers = _customer(client)
    chat = _chat(client, headers, "how to add a monitor in monitoring agent")
    assert chat["status"] == "active"
    assert _ai_lines(chat) == [ANSWER]
    prompt = client.model.answered_from()
    assert "monitoring agent" in prompt
    # The guide settles the subject: the model isn't told the monitor type is missing.
    assert "covered by the kb_guidance guide" in prompt and "not stated yet" not in prompt


def test_a_clear_how_to_with_no_entry_gets_the_friendly_reply_and_a_ticket(client):
    headers = _customer(client)
    chat = _chat(client, headers, "how do I add a mainframe monitor")
    assert chat["status"] == "escalated"
    lines = _ai_lines(chat)
    assert lines[0] == graph.NO_KB_REPLY_CHAT and chat["ticket_id"] in lines[1]
    assert "guess" not in lines[0] and "answer" not in client.model.ops()


# ---------------------------------------------------------------------------
# Guides specific to Linux / Windows
# ---------------------------------------------------------------------------
def test_os_specific_agent_guides_ask_linux_or_windows_first(client, kb):
    _mark_agent_guides_os_specific(kb)
    headers = _customer(client)
    chat = _chat(client, headers, "how to add a monitor in monitoring agent")
    assert chat["status"] == "active"
    assert _ai_lines(chat) == [graph.HOW_TO_VARIANT_QUESTION.format(options="Linux or Windows")]
    assert "answer" not in client.model.ops()
    chat = _send(client, headers, chat, "Windows")
    assert chat["status"] == "active" and _ai_lines(chat)[1] == ANSWER
    transcript = (f"Customer: how to add a monitor in monitoring agent\nAgent (AI): {_ai_lines(chat)[0]}\n"
                  "Customer: Windows")
    state = {"ticket_text": transcript}
    state.update(graph.retrieve_kb_candidates(state))
    assert graph.grounding_candidates(state)[0][0]["kb_entry_id"] == "KB-N0142"
    assert graph.how_to_question(state) is None  # never asked twice


def test_naming_the_os_up_front_skips_the_question(kb):
    _mark_agent_guides_os_specific(kb)
    assert _usable("how do I install the agent on windows server 2019")[:1] == ["KB-N0142"]
    state = {"ticket_text": "Customer: how do I install the agent on windows server 2019"}
    state.update(graph.retrieve_kb_candidates(state))
    assert graph.how_to_question(state) is None


# ---------------------------------------------------------------------------
# Routing order and the ticket form
# ---------------------------------------------------------------------------
def test_escalation_language_still_wins_over_the_how_to_question():
    state = {"ticket_text": "Customer: how to add the monitor, this is useless garbage",
             "confidence": 0.9, "category": "MONITORING",
             "language_tags": ["offensive_language"], "clarification_attempts": 0}
    assert graph.decide_route(state)["route"] == "human"


def test_the_question_is_never_asked_twice():
    text = f"Customer: How to add the monitor\nAgent (AI): {TYPE_QUESTION}\nCustomer: something else"
    state = {"ticket_text": text, "confidence": 0.9, "category": "MONITORING", "clarification_attempts": 1}
    assert graph.asked_how_to_question(text) and graph.how_to_question(state) is None
    assert graph.decide_route(state)["route"] == "ai"


def test_the_ticket_form_asks_by_email_then_answers(client):
    headers = _customer(client)
    res = client.post("/api/tickets", json={"subject": "New monitor", "description": "How to add the monitor",
                                            "priority": "medium"}, headers=headers)
    assert res.status_code == 200, res.text
    ticket_id = res.json()["ticket_id"]
    thread = client.get(f"/api/tickets/{ticket_id}", headers=headers).json()
    ai = [m["message"] for m in thread["messages"] if m["sender"] == "ai"]
    assert len(ai) == 1 and ai[0].startswith("Hi Dana,") and TYPE_QUESTION in ai[0]
    assert thread["ticket"]["status"] == "pending_customer"
    res = client.post(f"/api/tickets/{ticket_id}/reply", json={"message": "a website"}, headers=headers)
    assert res.status_code == 200, res.text
    thread = client.get(f"/api/tickets/{ticket_id}", headers=headers).json()
    ai = [m["message"] for m in thread["messages"] if m["sender"] == "ai"]
    assert len(ai) == 2 and ANSWER in ai[1]
    assert "answer" in client.model.ops()
