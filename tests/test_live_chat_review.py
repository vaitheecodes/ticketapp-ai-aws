"""Three live chats from the AWS deployment review, replayed locally.

A. "I was unable to login after changing license" (ACCOUNT, below the
   stricter bar): one clarifying question first (which login, what is on
   the screen), never KB topics or causes; the next turn raises the
   ticket. The hand-off has no "N/A", "Already tried: Nothing stated yet",
   no finding that restates the issue, and "No KB match - needs triage"
   instead of invented steps (license usage, plus.site24x7.com on 443).
B. "I need to add a APM monitor, how to do?" is a how-to with no KB entry
   (the only approved APM entries are troubleshooting): an honest "no guide"
   reply and a ticket, no improvised steps. A how-to that the KB does
   cover keeps only the steps the KB entry supports, with no "if you
   encounter issues, provide the exact error message" line.
C. "I need to add IOS app": no "are you referring to...?" (a how-to is
   clear), no "exact error message" (none was reported); the KB has no
   iOS entry, so the honest reply and a ticket. Monitor names in KB
   questions ("App-Server-04") no longer match "app".

Everything is real (FastAPI app, LangGraph pipeline, KB retrieval, SQLite)
except the language model: provider.complete is a scripted fake that
answers each operation the way the deployed model did.
"""

from __future__ import annotations

import itertools
import json

import pytest
from fastapi.testclient import TestClient

from backend import conversation, db, graph, kb_new_entries, kb_retrieval, main, request_kind, runtime
from backend.ai_service import chat_response, grounding, provider
from backend.ai_service.schemas import AnswerInput, ClarifyInput, HandoffSummary, KBCandidate
from backend.ai_service.summarize_conversation import NOTHING_TRIED, tidy_handoff

_counter = itertools.count()

A1 = "I was unable to login after changing license"
A_QUESTION = "Which login is it, the web console or the mobile app, and what appears on the screen?"
A2 = "The web console. After I sign in it goes straight back to the login page."
B = "I need to add a APM monitor, how to do?"
C = "I need to add IOS app"


class _Model:
    """Answers by operation, recognised from the trusted instructions."""

    def __init__(self, category="MONITORING", confidence=0.9):
        self.category, self.confidence = category, confidence
        self.calls: list[tuple[str, str, str]] = []  # (operation, system, user)
        self.answer = "Unused."
        self.handoff = {"issue": "Unused.", "tried": [], "findings": [], "unresolved_questions": []}

    def __call__(self, messages, **kwargs):
        system, user = messages[0]["content"], messages[1]["content"]

        def note(op):
            self.calls.append((op, system, user))

        if "You classify support tickets" in system:
            note("categorize")
            return json.dumps({"category": self.category, "confidence": self.confidence})
        if "reported a problem about their account, licensing" in system:
            note("clarify_high_risk")
            return A_QUESTION
        if "too ambiguous to classify confidently" in system:
            note("clarify")
            return "Are you referring to adding a monitor for an iOS application?"
        if "Rewrite the guidance in the kb_guidance block" in system:
            note("info_request")
            return "1. Open Admin > Data Export.\n2. Choose the date range and export."
        if "Site24x7 support assistant" in system or "helpful customer support agent" in system:
            note("answer")
            return self.answer
        if "Decide whether the customer's new message is a CONTINUATION" in system:
            note("continuation")
            return json.dumps({"is_continuation": False})
        if "taking over" in system:
            note("handoff_summary")
            return json.dumps(self.handoff)
        if "Suggest 2-5 concrete next steps" in system:
            note("suggest_steps")
            return json.dumps({"steps": ["Review current license usage and the monitors consuming licenses.",
                                         "Confirm the agent can reach plus.site24x7.com on 443."]})
        raise AssertionError(f"Unexpected AI operation: {system[:80]}")

    def ops(self) -> list[str]:
        return [op for op, _, _ in self.calls]

    def system_for(self, op: str) -> str:
        return next(system for name, system, _ in self.calls if name == op)


@pytest.fixture(autouse=True)
def _require_kb(tmp_path, monkeypatch):
    """The KB as it was reviewed: the how-to guides drafted afterwards
    (area "Getting started (how-to)", e.g. iOS and APM) kept as drafts,
    whatever has been approved locally since (test_how_to_flow.py covers
    them approved)."""
    data = json.loads(kb_new_entries.NEW_ENTRIES_PATH.read_text(encoding="utf-8"))
    for entry in data["entries"]:
        if entry.get("area") == "Getting started (how-to)":
            entry["status"] = "draft"
    path = tmp_path / "new_entries.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(kb_new_entries, "NEW_ENTRIES_PATH", path)
    kb_retrieval._load_index.cache_clear()
    if kb_retrieval._load_index() is None:
        pytest.skip("data/kb/kb_entries.json not found")
    yield
    kb_retrieval._load_index.cache_clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    model = _Model()
    monkeypatch.setattr(provider, "complete", model)
    with TestClient(main.app) as c:
        runtime.set_graph(graph.build_graph())  # the real routing pipeline
        c.model = model
        yield c


def _customer(client):
    res = client.post("/api/auth/customer/signup", json={
        "name": "Dana Smith", "email": f"review-{next(_counter)}@example.com", "password": "password123"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _chat(client, headers, issue):
    res = client.post("/api/chats", json={"issue": issue}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _send(client, headers, session_id, message):
    res = client.post(f"/api/chats/{session_id}/messages", json={"message": message}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _ticket(ticket_id):
    conn = db.get_connection()
    try:
        return dict(conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone())
    finally:
        conn.close()


def _ai_lines(chat):
    return [m["content"] for m in chat["messages"] if m["role"] == "ai"]


# ---------------------------------------------------------------------------
# A: high-risk login problem
# ---------------------------------------------------------------------------
def test_a_vague_account_report_gets_one_question_then_a_ticket(client):
    model = client.model
    model.category, model.confidence = "ACCOUNT", 0.81
    model.handoff = {  # what the deployed model wrote, plus one real new fact
        "issue": "The customer was unable to log in after changing their license.",
        "tried": ["N/A"],
        "findings": ["The customer is unable to log in after changing the license.",
                     "Signing in to the web console returns to the login page."],
        "unresolved_questions": ["N/A", "What exactly was changed on the license?"],
    }
    headers = _customer(client)

    # Turn 1: one question about what is unclear, no ticket yet.
    chat = _chat(client, headers, A1)
    assert chat["status"] == "active" and chat["ticket_id"] is None
    assert _ai_lines(chat) == [A_QUESTION]
    system = model.system_for("clarify_high_risk")
    assert "web console or the mobile app" in system
    clarify_call = json.dumps([c for c in model.calls if c[0] == "clarify_high_risk"])
    assert "KB-0275" not in clarify_call and "license usage" not in clarify_call  # no KB topics offered
    assert "answer" not in model.ops() and "clarify" not in model.ops()

    # Turn 2: still ACCOUNT below the bar -> the ticket, no second question.
    chat = _send(client, headers, chat["session_id"], A2)
    assert chat["status"] == "escalated" and model.ops().count("clarify_high_risk") == 1
    ticket = _ticket(chat["ticket_id"])
    brief = json.loads(ticket["ai_summary"])
    assert brief["tried"] == [NOTHING_TRIED]
    assert brief["findings"] == ["Signing in to the web console returns to the login page."]
    assert brief["unresolved_questions"] == ["What exactly was changed on the license?"]
    assert json.loads(ticket["ai_suggestions"]) == [conversation.NO_KB_STEPS]
    assert "suggest_steps" not in model.ops()
    for invented in ("plus.site24x7.com", "443", "license usage", "N/A"):
        assert invented not in ticket["ticket_text"]
    assert "Already tried:\n- Nothing stated yet" in ticket["ticket_text"]
    # The summary prompt asks for real unknowns, not placeholders.
    summary_prompt = model.system_for("handoff_summary")
    assert "never 'N/A'" in summary_prompt and "which login" in summary_prompt


def test_a_reported_error_on_a_high_risk_topic_goes_straight_to_a_human():
    state = {"ticket_text": "Customer: Login fails with error 'license expired' after changing license",
             "category": "ACCOUNT", "confidence": 0.81, "clarification_attempts": 0, "language_tags": []}
    state.update(graph.retrieve_kb_candidates(state))
    assert graph.decide_route(state)["route"] == "human"


def test_a_high_risk_escalate_with_info_entry_gives_its_steps_collect_list_and_note(monkeypatch):
    entry = {"kb_entry_id": "KB-T900", "category": "DATA", "intent": "data_export_request",
             "instruction": "How do I export my account data", "score": 0.6,
             "response": "Open Admin > Data Export.\nChoose the date range and export.",
             "handling": "escalate_with_info", "collect": ["Account owner email", "Date range needed"]}
    state = {"ticket_text": "Customer: I need my account data exported", "category": "DATA",
             "confidence": 0.6, "clarification_attempts": 0, "language_tags": [], "kb_candidates": [entry]}
    route = graph.decide_route(state)
    assert route["route"] == "ai" and "escalate_with_info" in route["route_reason"]

    monkeypatch.setattr(provider, "complete_text", lambda *a, **k: (
        "1. Open Admin > Data Export.\n2. Choose the date range and export."))
    reply = graph.generate_response({**state, "reply_format": "chat"})
    assert reply["needs_info"]["kb_entry_id"] == "KB-T900"
    assert graph.handoff_reason(reply["needs_info"]) == "kb_needs_info"
    text = reply["ai_response"]
    assert "1. Open Admin > Data Export." in text and "2. Choose the date range" in text
    assert "Please add these to your ticket:\n- Account owner email\n- Date range needed" in text
    assert text.endswith(chat_response.INFO_SAFETY_NOTE)


# ---------------------------------------------------------------------------
# B: how-to requests
# ---------------------------------------------------------------------------
def test_b_apm_how_to_with_no_kb_entry_says_so_and_raises_a_ticket(client):
    model = client.model
    model.category, model.confidence = "APM", 0.9
    model.handoff = {"issue": "The customer wants to add an APM monitor.", "tried": [], "findings": [],
                     "unresolved_questions": ["Which application stack is it (Java, .NET, Node.js)?"]}
    headers = _customer(client)
    chat = _chat(client, headers, B)
    assert chat["status"] == "escalated"
    first, ticket_line = _ai_lines(chat)
    assert first == graph.NO_KB_REPLY_CHAT and chat["ticket_id"] in ticket_line
    assert "answer" not in model.ops()  # no improvised steps
    ticket = _ticket(chat["ticket_id"])
    assert json.loads(ticket["ai_suggestions"]) == [conversation.NO_KB_STEPS]
    assert "Admin > Applications" not in json.dumps(chat)
    reason = next(user for op, _, user in model.calls if op == "handoff_summary")
    assert "No knowledge-base article covers this request" in reason


def test_b_apm_entries_are_troubleshooting_so_they_never_ground_a_how_to():
    assert request_kind.is_how_to(B)
    state = {"ticket_text": f"Customer: {B}"}
    state.update(graph.retrieve_kb_candidates(state))
    usable, _ = graph.grounding_candidates(state)
    assert not any(c["intent"] == "apm_troubleshooting" for c in usable)
    assert usable == []


def test_b_a_covered_how_to_keeps_only_kb_steps_and_no_error_boilerplate(client):
    model = client.model
    model.category, model.confidence = "CONTAINERS", 0.9
    model.answer = (
        "1. Verify the Docker installation and agent permissions.\n"
        "2. Go to Admin > Applications > APM and click Add Monitor.\n"
        "3. Confirm Docker socket access and container discovery.\n"
        "If you encounter issues, please provide the exact error message.")
    headers = _customer(client)
    chat = _chat(client, headers, "how do I configure docker monitoring")
    assert chat["status"] == "active"
    (reply,) = _ai_lines(chat)
    assert reply == ("1. Verify the Docker installation and agent permissions.\n"
                     "2. Confirm Docker socket access and container discovery.")
    system = model.system_for("answer")
    assert "how-to request" in system and "exact error text" not in system


# ---------------------------------------------------------------------------
# C: "I need to add IOS app"
# ---------------------------------------------------------------------------
def test_c_ios_app_is_not_clarified_and_gets_an_honest_ticket(client):
    model = client.model
    model.category, model.confidence = "MONITORING", 0.4  # the model was unsure
    model.handoff = {"issue": "The customer wants to add an iOS app.", "tried": [],
                     "findings": [], "unresolved_questions": ["Is it mobile app monitoring or a website check?"]}
    headers = _customer(client)
    chat = _chat(client, headers, C)
    assert chat["status"] == "escalated"
    text = json.dumps(_ai_lines(chat))
    assert "referring to" not in text and "error message" not in text
    assert _ai_lines(chat)[0] == graph.NO_KB_REPLY_CHAT
    assert "clarify" not in model.ops() and "answer" not in model.ops()
    assert json.loads(_ticket(chat["ticket_id"])["ai_suggestions"]) == [conversation.NO_KB_STEPS]


def test_c_monitor_names_in_kb_questions_do_not_match_app():
    state = {"ticket_text": f"Customer: {C}"}
    state.update(graph.retrieve_kb_candidates(state))
    assert state["kb_candidates"] == []
    assert kb_retrieval._index_tokens("Why is my site24x7 monitor showing down for App-Server-04") == [
        "site24x7", "monitor", "showing"]


def test_c_the_ticket_path_gives_the_same_honest_reply(client):
    client.model.category, client.model.confidence = "MONITORING", 0.4
    client.model.handoff = {"issue": "Add an iOS app.", "tried": [], "findings": [], "unresolved_questions": []}
    headers = _customer(client)
    res = client.post("/api/tickets", json={"subject": "iOS app", "description": C, "priority": "medium"},
                      headers=headers)
    assert res.status_code == 200, res.text
    ticket = _ticket(res.json()["ticket_id"])
    assert ticket["route"] == "human" and ticket["status"] == "open"
    thread = client.get(f"/api/tickets/{ticket['ticket_id']}", headers=headers).json()["messages"]
    assert any(graph.NO_KB_REPLY_EMAIL in m["message"] for m in thread if m["sender"] == "ai")
    assert json.loads(ticket["ai_suggestions"]) == [conversation.NO_KB_STEPS]


# ---------------------------------------------------------------------------
# The filters on their own
# ---------------------------------------------------------------------------
def test_error_message_is_asked_for_only_after_an_error_was_reported():
    reply = "1. Open the monitor.\nIf you see anything odd, what is the exact error message?"
    assert "error" not in chat_response.clean_chat_reply(reply, error_reported=False)
    assert "exact error message" in chat_response.clean_chat_reply(reply, error_reported=True)
    assert chat_response.detail_question(True, False) == chat_response.STEP_QUESTION
    assert chat_response.detail_question(True, True) == chat_response.DETAIL_QUESTION
    assert chat_response.detail_question(False, True) == chat_response.MONITOR_TYPE_QUESTION
    assert not graph.error_reported(f"Customer: {C}\nAgent (AI): ok\nCustomer: yes")
    assert graph.error_reported("Customer: adding the monitor fails with error 401")


@pytest.mark.parametrize("text, how_to", [
    (B, True), (C, True), ("how do I configure docker monitoring", True), ("add a website monitor", True),
    (A1, False), ("my APM monitor is showing down", False), ("adding the monitor fails", False),
    ("what is the data retention period", False),
])
def test_how_to_detection(text, how_to):
    assert request_kind.is_how_to(text) is how_to


def test_how_to_boilerplate_is_dropped_only_for_how_to():
    reply = "Use the Add Monitor form.\nIf you encounter issues, let me know."
    assert chat_response.clean_chat_reply(reply, how_to=True) == "Use the Add Monitor form."
    assert "encounter" in chat_response.clean_chat_reply(reply, how_to=False)


@pytest.mark.parametrize("step, ok", [
    ("Verify the Docker installation and agent permissions.", True),
    ("Go to Admin > Applications > APM and click Add.", False),       # path not in the KB
    ("Confirm the agent can reach plus.site24x7.com on 443.", False),  # host and port not in the KB
    ("Each address can receive up to 500 alert emails a day.", False),  # number not in the KB
    ("Restart the Kubernetes cluster and reinstall Helm charts.", False),
])
def test_steps_must_be_supported_by_the_kb_entry(step, ok):
    kb = ("For Docker monitoring, verify the Docker installation, agent permissions, Docker socket access, "
          "and container discovery.")
    assert grounding.supported(step, kb) is ok


def test_menu_paths_from_the_kb_are_kept():
    kb = "Make sure the user's mobile number is linked under Admin > User & Alert Management."
    assert grounding.supported("Go to Admin > User & Alert Management and link the mobile number.", kb)


def test_an_answer_with_only_invented_steps_is_regenerated_not_sent(monkeypatch):
    replies = iter(["1. Go to Admin > Applications > APM.\n2. Click Add Monitor.",
                    "Docker monitoring needs agent permissions and Docker socket access."])
    monkeypatch.setattr(provider, "complete_text", lambda *a, **k: next(replies))
    kb = KBCandidate(kb_entry_id="KB-0048", category="CONTAINERS", intent="docker_monitoring",
                     instruction="How do i configure docker monitoring", score=1.0,
                     response="For Docker monitoring, verify the Docker installation, agent permissions, "
                              "Docker socket access, and container discovery.")
    data = AnswerInput(ticket_text="Customer: how do I configure docker monitoring", kb_candidates=[kb],
                       how_to=True, error_reported=False, latest_message="how do I configure docker monitoring")
    assert chat_response.answer(data).text == "Docker monitoring needs agent permissions and Docker socket access."


def test_handoff_tidy_drops_placeholders_and_restatements():
    summary = HandoffSummary(
        issue="Unable to login after changing license.",
        tried=["N/A"], findings=["Unable to login after changing the license", "none"],
        unresolved_questions=["N/A", "Which login: web console or mobile app?"])
    tidy = tidy_handoff(summary, A1)
    assert tidy.tried == [NOTHING_TRIED] and tidy.findings == []
    assert tidy.unresolved_questions == ["Which login: web console or mobile app?"]
    assert "Findings" not in tidy.as_text([conversation.NO_KB_STEPS])


def test_handoff_steps_come_only_from_a_matching_kb_entry():
    assert graph.handoff_kb_candidates(A1) == []  # "license" alone is no match
    hits = graph.handoff_kb_candidates("how do I configure docker monitoring")
    assert [c["kb_entry_id"] for c in hits][:1] == ["KB-0048"]


def test_high_risk_clarify_gets_no_kb_topics_or_monitor_type_question(monkeypatch):
    seen = {}

    def fake(instructions, blocks, operation="", **kwargs):
        seen.update(instructions=instructions, blocks=blocks)
        return A_QUESTION

    monkeypatch.setattr(provider, "complete_text", fake)
    state = {"ticket_text": f"Customer: {A1}", "category": "ACCOUNT", "confidence": 0.81, "reply_format": "chat"}
    state.update(graph.retrieve_kb_candidates(state))
    assert graph.ask_clarifying_question(state)["ai_response"] == A_QUESTION
    assert "candidate_topics" not in seen["blocks"]
    assert chat_response._MONITOR_TYPE_FIRST not in seen["instructions"]
    assert ClarifyInput(ticket_text="x").high_risk is False
