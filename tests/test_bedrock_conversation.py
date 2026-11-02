"""Bedrock Converse message rules across a multi-turn conversation.

Converse rejects (ValidationException) a request whose first message
isn't from the user, whose roles don't alternate, or that has a blank
text block. backends.converse_messages builds every request to those
rules; _StrictClient is a fake bedrock-runtime client that enforces them
the way the real API does, so the tests fail if any request breaks them.

Multi-turn: a chat whose second turn failed (throttled) leaves two
customer messages in a row; a ticket thread holds system events and
internal notes. The next turn's request must still be valid, carry the
whole public conversation, and never the banner or internal text.
No AWS calls: the client is a fake.
"""

from __future__ import annotations

import itertools

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from backend import chats, db, main
from backend.ai_service import backends, provider
from backend.ai_service.backends import BedrockBackend, BackendError, converse_messages

_counter = itertools.count()
# Captured at import, before conftest's autouse fixture disables it.
_REAL_COMPLETE = provider.complete
INTERNAL_NOTE = "INTERNAL-NOTE-do-not-send"


def _validation_error(message):
    return ClientError({"Error": {"Code": "ValidationException", "Message": message}}, "Converse")


def converse_violations(request: dict) -> list[str]:
    """The Converse API's message rules, as the real API applies them."""
    problems = []
    messages = request.get("messages") or []
    if not messages:
        problems.append("no messages")
    if messages and messages[0].get("role") != "user":
        problems.append("first message is not from the user")
    for i, (a, b) in enumerate(zip(messages, messages[1:])):
        if a.get("role") == b.get("role"):
            problems.append(f"messages {i} and {i + 1} have the same role")
    for i, message in enumerate(messages):
        if message.get("role") not in ("user", "assistant"):
            problems.append(f"message {i} has role {message.get('role')!r}")
        if not message.get("content"):
            problems.append(f"message {i} has no content")
        for j, block in enumerate(message.get("content") or []):
            if "text" in block and not block["text"].strip():
                problems.append(f"messages.{i}.content.{j} text is blank")
    for j, block in enumerate(request.get("system") or []):
        if not block.get("text", "").strip():
            problems.append(f"system.{j} text is blank")
    return problems


class _StrictClient:
    """Rejects requests that break the Converse rules; otherwise answers
    forced tool calls by tool name and text requests with `text`.
    `fail_next`: exceptions raised by the next calls, one per call."""

    TOOL_REPLIES = {
        "Categorization": {"category": "AGENT", "confidence": 0.95},
        "ContinuationVerdict": {"is_continuation": True},
    }

    def __init__(self, text="Restart the agent service."):
        self.text = text
        self.calls, self.rejected = [], []
        self.fail_next = []

    def converse(self, **request):
        self.calls.append(request)
        if self.fail_next:
            raise self.fail_next.pop(0)
        problems = converse_violations(request)
        if problems:
            self.rejected.append(problems)
            raise _validation_error("; ".join(problems))
        tool = request.get("toolConfig", {}).get("tools", [{}])[0].get("toolSpec")
        if tool:
            data = self.TOOL_REPLIES.get(tool["name"]) or backends._sample(
                tool["inputSchema"]["json"], tool["inputSchema"]["json"].get("$defs", {}))
            content = [{"toolUse": {"toolUseId": "t1", "name": tool["name"], "input": data}}]
        else:
            content = [{"text": self.text}]
        return {"output": {"message": {"role": "assistant", "content": content}},
                "stopReason": "end_turn", "usage": {}, "metrics": {}}

    def user_text(self, call: dict) -> str:
        return "\n".join(b["text"] for m in call["messages"] for b in m["content"] if "text" in b)


@pytest.fixture()
def strict(monkeypatch):
    monkeypatch.setattr(provider, "complete", _REAL_COMPLETE)
    monkeypatch.setattr(backends, "_sleep", lambda seconds: None)
    client = _StrictClient()
    provider.set_backend(BedrockBackend(client=client))
    return client


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with TestClient(main.app) as c:
        yield c


def _customer(client):
    res = client.post("/api/auth/customer/signup", json={
        "name": "Dana Smith", "email": f"bedrock-conv-{next(_counter)}@example.com", "password": "password123"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


# ---------------------------------------------------------------------------
# converse_messages and the request builder
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("turns, expected_roles, expected_texts", [
    ([("user", "a")], ["user"], [["a"]]),
    ([("assistant", "hi"), ("user", "a")], ["user"], [["a"]]),
    ([("user", "a"), ("user", "b"), ("assistant", "c"), ("assistant", "d"), ("user", "e")],
     ["user", "assistant", "user"], [["a", "b"], ["c", "d"], ["e"]]),
    ([("user", "a"), ("assistant", "  "), ("user", "b")], ["user"], [["a", "b"]]),
    ([("user", ""), ("assistant", "x"), ("user", "\n"), ("user", "b")], ["user"], [["b"]]),
])
def test_converse_messages_follow_the_api_rules(turns, expected_roles, expected_texts):
    messages = converse_messages(turns)
    assert [m["role"] for m in messages] == expected_roles
    assert [[b["text"] for b in m["content"]] for m in messages] == expected_texts
    assert converse_violations({"messages": messages}) == []


@pytest.mark.parametrize("turns", [[], [("user", "  ")], [("assistant", "only the model")]])
def test_converse_messages_refuse_a_request_with_no_user_text(turns):
    with pytest.raises(BackendError):
        converse_messages(turns)


def test_converse_messages_refuse_unknown_roles():
    with pytest.raises(BackendError):
        converse_messages([("user", "a"), ("system", "b")])


def test_request_drops_blank_correction_notes_and_system():
    request = BedrockBackend.request("   ", "data", max_tokens=10, json_schema=None,
                                     extra_messages=("", "Fix the reply.", " \n"), model_id="m")
    assert converse_violations(request) == []
    assert "system" not in request
    assert [b["text"] for b in request["messages"][0]["content"]] == ["data", "Fix the reply."]


def test_the_strict_fake_rejects_what_bedrock_rejects(strict):
    bad = {"modelId": "m", "messages": [
        {"role": "assistant", "content": [{"text": "x"}]},
        {"role": "user", "content": [{"text": " "}]},
        {"role": "user", "content": [{"text": "y"}]}]}
    with pytest.raises(ClientError):
        strict.converse(**bad)
    assert strict.rejected == [[
        "first message is not from the user", "messages 1 and 2 have the same role",
        "messages.1.content.0 text is blank"]]


# ---------------------------------------------------------------------------
# Multi-turn conversations through the real pipeline
# ---------------------------------------------------------------------------
def test_chat_turn_after_an_ai_failure_sends_a_valid_request(app_client, strict):
    headers = _customer(app_client)
    strict.text = "Hello Dana,\n\nRestart the agent service.\n\nBest regards,\n[Your Name]\nSite24x7 Support Team"
    first = app_client.post("/api/chats", json={"issue": "My Linux agent stopped sending data"},
                            headers=headers).json()
    assert first["ai_error"] is None
    assert first["messages"][-1] == {**first["messages"][-1], "role": "ai", "content": "Restart the agent service."}
    session_id = first["session_id"]

    # Turn 2: Bedrock throttles twice (the call and its one retry). The
    # customer's message is kept, nothing else.
    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse")
    strict.fail_next = [throttled, throttled]
    second = app_client.post(f"/api/chats/{session_id}/messages",
                             json={"message": "Which log file should I check?"}, headers=headers).json()
    assert second["ai_error"] == chats.AI_UNAVAILABLE
    assert [m["role"] for m in second["messages"]] == ["customer", "ai", "customer"]

    # Turn 3: two customer messages in a row in the history.
    strict.text = "Check agent.log in the agent's logs folder."
    calls_before = len(strict.calls)
    third = app_client.post(f"/api/chats/{session_id}/messages",
                            json={"message": "Where is that log on Linux?"}, headers=headers).json()
    assert third["ai_error"] is None and third["status"] == "active"
    assert [m["role"] for m in third["messages"]] == ["customer", "ai", "customer", "customer", "ai"]
    assert third["messages"][-1]["content"] == "Check agent.log in the agent's logs folder."

    assert strict.rejected == []
    answer_request = strict.calls[-1]
    assert len(strict.calls) > calls_before and "toolConfig" not in answer_request
    assert converse_violations(answer_request) == []
    sent = strict.user_text(answer_request)
    assert "Which log file should I check?" in sent and "Where is that log on Linux?" in sent
    assert chats.AI_UNAVAILABLE not in sent


def test_ticket_reply_history_with_system_events_and_notes_is_valid(app_client, strict):
    headers = _customer(app_client)
    created = app_client.post("/api/tickets", json={
        "subject": "Agent not reporting", "description": "My Linux agent stopped sending data"},
        headers=headers)
    assert created.status_code == 200, created.text
    ticket_id = created.json()["ticket_id"]

    conn = db.get_connection()
    try:
        for i, (sender, visibility, text) in enumerate([
                ("system", "system_event", "Status changed to Pending Customer"),
                ("staff", "internal_note", INTERNAL_NOTE),
                ("system", "system_event", "   ")]):
            conn.execute(
                "INSERT INTO ticket_messages (message_id, ticket_id, sender, message, created_at, visibility, "
                "customer_visible) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"MSG-test-{ticket_id}-{i}", ticket_id, sender, text, "2099-01-01T00:00:0%d+00:00" % i,
                 visibility, 0 if visibility == "internal_note" else 1))
        conn.commit()
    finally:
        conn.close()

    strict.text = "Check agent.log in the agent's logs folder."
    res = app_client.post(f"/api/tickets/{ticket_id}/reply",
                          json={"message": "Which log file should I check?"}, headers=headers)
    assert res.status_code == 200, res.text
    assert strict.rejected == []
    assert all(converse_violations(call) == [] for call in strict.calls)
    assert not any(INTERNAL_NOTE in strict.user_text(call) for call in strict.calls)
