"""AI failures are observable without a terminal, and our own checks never
show the customer "AI unavailable".

- Every failure of a turn is recorded on the chat (audit log,
  chat.ai_failure): kind, error code name, attempts, latency, operation,
  outcome. Never AWS message text or customer text. Admin only.
- A reply rejected by our own filter or schema check is regenerated or
  replaced by a useful question; a failed continuation check falls
  through to the normal pipeline. Provider errors still show the banner.

The provider is a strict fake bedrock-runtime client: no AWS calls.
"""

from __future__ import annotations

import itertools
import json

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError
from fastapi.testclient import TestClient

from backend import chats, db, main, runtime
from backend.ai_service import AIError, backends, chat_response, provider
from backend.ai_service.backends import BedrockBackend
from tests.test_bedrock_conversation import _REAL_COMPLETE, _StrictClient

_counter = itertools.count()
AWS_TEXT = "User arn:aws:iam::111122223333:user/SECRET-USER is not authorized"
CUSTOMER_TEXT = "my agent stopped reporting on host zebracorn-prod-7"


def _error(code):
    return ClientError({"Error": {"Code": code, "Message": AWS_TEXT}}, "Converse")


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
        "name": "Dana Smith", "email": f"fail-{next(_counter)}@example.com", "password": "password123"})
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _admin(client):
    token = client.post("/api/auth/login", json={"email": "admin@example.com",
                                                 "password": "admin-dev-password"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _audit_rows(session_id):
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT action, entity, entity_id, actor_id, detail FROM audit_events WHERE entity_id = ? "
            "ORDER BY id", (session_id,))]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Failure kinds from the provider
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("error, kind, code, attempts", [
    (_error("ThrottlingException"), "throttling", "ThrottlingException", 2),
    (_error("ModelTimeoutException"), "timeout", "ModelTimeoutException", 2),
    (ReadTimeoutError(endpoint_url="https://bedrock-runtime.ap-south-1.amazonaws.com"), "timeout",
     "ReadTimeoutError", 2),
    (_error("AccessDeniedException"), "access_denied", "AccessDeniedException", 1),
    (_error("ValidationException"), "validation", "ValidationException", 1),
    (_error("ResourceNotFoundException"), "config", "ResourceNotFoundException", 1),
    (NoCredentialsError(), "config", "NoCredentialsError", 1),
    (_error("InternalServerException"), "unknown", "InternalServerException", 1),
])
def test_provider_failures_carry_a_safe_reason(strict, error, kind, code, attempts):
    strict.fail_next = [error, error]
    log = provider.start_failure_log()
    with pytest.raises(AIError) as exc:
        provider.complete_text("instructions", {"ticket": CUSTOMER_TEXT}, operation="answer")
    failure = exc.value.failure
    assert (failure.kind, failure.code, failure.attempts, failure.operation, failure.outcome) == (
        kind, code, attempts, "answer", "failed")
    assert isinstance(failure.latency_ms, int) and failure.latency_ms >= 0
    assert log == [failure]
    stored = json.dumps(failure.detail())
    assert "SECRET-USER" not in stored and "zebracorn" not in stored


def test_a_schema_failure_is_parse_schema(strict):
    from backend.ai_service.schemas import ContinuationVerdict

    strict.TOOL_REPLIES = {"ContinuationVerdict": {"is_continuation": "maybe"}}
    with pytest.raises(AIError) as exc:
        provider.complete_json("decide", {"m": "x"}, ContinuationVerdict)
    assert (exc.value.failure.kind, exc.value.failure.code, exc.value.failure.attempts) == (
        "parse_schema", "schema_mismatch", 2)


def test_error_codes_are_names_only():
    assert backends.safe_code("ThrottlingException") == "ThrottlingException"
    assert backends.safe_code("HTTP_429") == "HTTP_429"
    assert backends.safe_code(AWS_TEXT) is None
    assert backends.safe_code("") is None


# ---------------------------------------------------------------------------
# Recorded on the chat, shown to the admin only
# ---------------------------------------------------------------------------
def test_a_provider_failure_is_recorded_on_the_chat(app_client, strict):
    headers = _customer(app_client)
    first = app_client.post("/api/chats", json={"issue": "My Linux agent stopped sending data"},
                            headers=headers).json()
    assert first["ai_error"] is None
    sid = first["session_id"]
    strict.fail_next = [_error("ThrottlingException")] * 2
    second = app_client.post(f"/api/chats/{sid}/messages", json={"message": CUSTOMER_TEXT}, headers=headers).json()
    assert second["ai_error"] == chats.AI_UNAVAILABLE

    rows = [r for r in _audit_rows(sid) if r["action"] == "chat.ai_failure"]
    assert len(rows) == 1 and rows[0]["entity"] == "chat" and rows[0]["actor_id"] == "system"
    detail = json.loads(rows[0]["detail"])
    assert {k: detail[k] for k in ("kind", "code", "attempts", "outcome")} == {
        "kind": "throttling", "code": "ThrottlingException", "attempts": 2, "outcome": "failed"}
    assert detail["operation"] == "ContinuationVerdict" and isinstance(detail["latency_ms"], int)
    assert "SECRET-USER" not in rows[0]["detail"] and "zebracorn" not in rows[0]["detail"]

    view = app_client.get(f"/api/admin/chats/{sid}", headers=_admin(app_client))
    assert view.status_code == 200
    body = view.json()
    assert [f["kind"] for f in body["ai_failures"]] == ["throttling"]
    assert body["message_counts"] == {"customer": 2, "ai": 1}
    assert "zebracorn" not in view.text
    assert app_client.get(f"/api/admin/chats/{sid}", headers=headers).status_code in (401, 403)
    audit = app_client.get("/api/admin/audit", params={"entity": "chat", "entity_id": sid},
                           headers=_admin(app_client)).json()
    assert [e["action"] for e in audit["events"]] == ["chat.ai_failure"]


def test_a_bug_in_the_turn_is_recorded_as_unknown(app_client, monkeypatch):
    headers = _customer(app_client)

    class Broken:
        def invoke(self, state):
            raise RuntimeError("bug with " + CUSTOMER_TEXT)

    runtime.set_graph(Broken())
    res = app_client.post("/api/chats", json={"issue": "My agent is offline"}, headers=headers).json()
    assert res["ai_error"] == chats.AI_UNAVAILABLE
    rows = [json.loads(r["detail"]) for r in _audit_rows(res["session_id"]) if r["action"] == "chat.ai_failure"]
    assert rows == [{"kind": "unknown", "operation": "turn", "code": "RuntimeError", "attempts": 1,
                     "latency_ms": 0, "outcome": "failed"}]


def test_our_filter_rejecting_the_reply_is_not_ai_unavailable(app_client, strict):
    headers = _customer(app_client)
    strict.text = "Hello [Customer Name],\n\n[Your answer here]\n\nBest regards,\n[Your Name]"
    res = app_client.post("/api/chats", json={"issue": "My Linux agent stopped sending data"},
                          headers=headers).json()
    assert res["ai_error"] is None and res["status"] == "active"
    # No error was reported ("stopped sending data"): never the exact error message.
    assert res["messages"][-1]["content"] == chat_response.STEP_QUESTION
    outcomes = [(json.loads(r["detail"])["kind"], json.loads(r["detail"])["outcome"])
                for r in _audit_rows(res["session_id"]) if r["action"] == "chat.ai_failure"]
    assert outcomes == [("filter_rejected", "regenerated"), ("filter_rejected", "fallback")]


def test_a_bad_continuation_verdict_falls_through_to_the_pipeline(app_client, strict):
    headers = _customer(app_client)
    first = app_client.post("/api/chats", json={"issue": "My Linux agent stopped sending data"},
                            headers=headers).json()
    strict.TOOL_REPLIES = {**_StrictClient.TOOL_REPLIES, "ContinuationVerdict": {"is_continuation": "maybe"}}
    strict.text = "Check the agent log in the agent's logs folder."
    second = app_client.post(f"/api/chats/{first['session_id']}/messages",
                             json={"message": "Where is the agent log on Linux?"}, headers=headers).json()
    assert second["ai_error"] is None
    assert second["messages"][-1]["content"] == "Check the agent log in the agent's logs folder."
    rows = [json.loads(r["detail"]) for r in _audit_rows(first["session_id"]) if r["action"] == "chat.ai_failure"]
    assert [(r["kind"], r["operation"], r["outcome"]) for r in rows] == [
        ("parse_schema", "ContinuationVerdict", "fallback")]


def test_a_ticket_follow_up_failure_is_recorded_on_the_ticket(app_client, strict):
    headers = _customer(app_client)
    created = app_client.post("/api/tickets", json={"subject": "Agent install",
                                                    "description": "How do I install the Site24x7 agent on Linux?"},
                              headers=headers).json()
    assert created["route"] == "ai"
    strict.fail_next = [_error("AccessDeniedException")]
    res = app_client.post(f"/api/tickets/{created['ticket_id']}/reply",
                          json={"message": "Which log file should I check?"}, headers=headers).json()
    assert res.get("ai_error")
    rows = [json.loads(r["detail"]) for r in _audit_rows(created["ticket_id"]) if r["action"] == "ticket.ai_failure"]
    assert [(r["kind"], r["code"], r["attempts"]) for r in rows] == [("access_denied", "AccessDeniedException", 1)]
