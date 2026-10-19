"""Post-incident summaries and incident memory
(backend/incident_resolution.py, backend/incident_memory.py,
ai_service.draft_post_incident / incident_question).

Real temporary SQLite DB via FastAPI's TestClient. The model is mocked:
correlation via the `judge` fixture, the post-incident draft by replacing
provider.complete (so schema validation and the no-invented-facts check
still run), and question parsing by replacing parse_incident_question.
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import app_settings, db, graph, email_utils, incident_memory, incident_resolution, main, runtime, seed_data
from backend.ai_service import correlate_incidents, draft_post_incident, incident_question, provider
from backend.ai_service.schemas import CorrelationJudgement, IncidentQuestion
from backend.timeutil import format_ist

_counter = itertools.count()

GOOD_DRAFT = {
    "what_happened": "Server monitors stopped collecting data for some customers.",
    "what_was_fixed": "The team restarted the data collectors and backfilled the gap.",
    "what_customers_should_do": "Nothing — your monitoring is back to normal.",
    "internal_summary": "Collector fleet stalled; restart and backfill resolved it.",
}


class _Pipeline:
    """route 'human' for tickets that should join an incident; 'ai' for
    chats/tickets the AI keeps."""

    def __init__(self):
        self.route = "human"

    def invoke(self, state):
        if self.route == "ai":
            return {"route": "ai", "category": "SERVER_MONITORING", "confidence": 0.95,
                    "ai_response": "Here is how to set that up.", "clarification_attempts": 0}
        return {"route": "human", "category": "SERVER_MONITORING", "confidence": 0.5,
                "clarification_attempts": 0}


@pytest.fixture()
def pipeline():
    return _Pipeline()


@pytest.fixture()
def client(tmp_path, monkeypatch, pipeline):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with TestClient(main.app) as c:
        runtime.set_graph(pipeline)
        yield c


@pytest.fixture(autouse=True)
def judge(monkeypatch):
    def _fake(data):
        return CorrelationJudgement(
            is_related=True, related_ticket_ids=[t.ticket_id for t in data.tickets],
            why_related="Same symptom", title="Server monitors not collecting data",
            confidence=0.9, candidate_departments=[], observed_facts=["3 reports of no data"],
            hypotheses=["Possible collector issue"],
        )
    monkeypatch.setattr(correlate_incidents, "correlate_incidents", _fake)


@pytest.fixture(autouse=True)
def _no_continuations(monkeypatch):
    monkeypatch.setattr(graph, "is_continuation_of_prior_response", lambda *a, **k: False)


@pytest.fixture()
def model(monkeypatch):
    """provider.complete for the draft: returns queued outputs, records prompts."""
    state = {"outputs": [], "prompts": []}

    def _complete(messages, temperature=0.0, json_mode=False):
        if "post-incident summary" not in messages[0]["content"]:
            raise provider.AIError("only the post-incident draft is mocked")
        state["prompts"].append(messages)
        return json.dumps(state["outputs"].pop(0) if state["outputs"] else GOOD_DRAFT)

    monkeypatch.setattr(provider, "complete", _complete)
    return state


@pytest.fixture()
def asks(monkeypatch):
    """Every incident question asks about the last 3 hours."""
    calls = []

    def _parse(data):
        calls.append(data)
        end = datetime.now(timezone.utc)
        return IncidentQuestion(is_incident_question=True, window_start=end - timedelta(hours=3),
                                window_end=end, category=None, topic=None)

    monkeypatch.setattr(incident_question, "parse_incident_question", _parse)
    return calls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _signup(client):
    n = next(_counter)
    res = client.post("/api/auth/customer/signup", json={
        "name": f"Customer Name{n}", "email": f"res-{n}@example.com", "password": "password123"})
    return _bearer(res.json()["token"]), res.json()


def _login(client, email, password):
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return _bearer(res.json()["token"])


def _admin(client):
    return _login(client, "admin@example.com", "admin-dev-password")


def _manager(client):
    (p,) = [p for p in seed_data.DEMO_STAFF if p["role"] == "manager"]
    return _login(client, p["email"], p["password"])


def _tech(client):
    (p,) = [p for p in seed_data.DEMO_STAFF if p["role"] == "technician"]
    return _login(client, p["email"], p["password"]), p


def _query(sql, params=()):
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _exec(sql, params=()):
    conn = db.get_connection()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _incident(client, confirm=True):
    """Three customers report the same problem; returns (incident_id,
    [(headers, ticket_id)])."""
    reporters = []
    for _ in range(3):
        headers, _ = _signup(client)
        res = client.post("/api/tickets", json={
            "subject": "Server monitor not collecting data",
            "description": "No data from our Linux servers."}, headers=headers)
        assert res.status_code == 200, res.text
        reporters.append((headers, res.json()["ticket_id"]))
    (cluster,) = _query("SELECT * FROM incident_clusters")
    if confirm:
        res = client.post(f"/api/incidents/{cluster['incident_id']}/review",
                          json={"action": "confirm", "note": "Real outage"}, headers=_admin(client))
        assert res.status_code == 200, res.text
    return cluster["incident_id"], reporters


def _fixed_at():
    return datetime.now(timezone.utc).isoformat()


def _draft(client, incident_id, headers, root_cause=None):
    return client.post(f"/api/incidents/{incident_id}/resolution/draft", json={
        "resolution_note": "Restarted the data collectors and backfilled missing data.",
        "fixed_at": _fixed_at(), "root_cause": root_cause}, headers=headers)


def _send(client, incident_id, headers, **extra):
    summary = _query("SELECT customer_summary FROM incident_clusters WHERE incident_id = ?",
                     (incident_id,))[0]["customer_summary"]
    return client.post(f"/api/incidents/{incident_id}/resolution/send",
                       json={"customer_summary": summary or "x", **extra}, headers=headers)


def _updates(ticket_id):
    return _query("SELECT * FROM ticket_messages WHERE ticket_id = ? AND subject = ?",
                  (ticket_id, incident_resolution.UPDATE_SUBJECT))


def _resolved_incident(client, model, visibility=None):
    incident_id, reporters = _incident(client)
    manager = _manager(client)
    assert _draft(client, incident_id, manager).status_code == 200
    if visibility:
        client.put(f"/api/incidents/{incident_id}/visibility", json={"visibility": visibility},
                   headers=manager)
    assert _send(client, incident_id, manager).status_code == 200
    return incident_id, reporters


def _ask_in_chat(client, headers, pipeline, question="Was there an outage today?"):
    pipeline.route = "ai"
    chat = client.post("/api/chats", json={"issue": "How do I add a monitor?"}, headers=headers).json()
    res = client.post(f"/api/chats/{chat['session_id']}/messages", json={"message": question},
                      headers=headers)
    assert res.status_code == 200, res.text
    return [m for m in res.json()["messages"] if m["role"] == "ai"][-1]["content"]


def _actions(incident_id):
    return [r["action"] for r in _query(
        "SELECT action FROM audit_events WHERE entity = 'incident' AND entity_id = ? ORDER BY id",
        (incident_id,))]


# ---------------------------------------------------------------------------
# Resolving
# ---------------------------------------------------------------------------
def test_cannot_resolve_an_unconfirmed_incident(client, model):
    incident_id, _ = _incident(client, confirm=False)
    res = _draft(client, incident_id, _manager(client))
    assert res.status_code == 400
    assert model["prompts"] == []
    assert _send(client, incident_id, _manager(client)).status_code == 400


def test_only_manager_admin_or_assigned_technician_can_resolve(client, model):
    incident_id, reporters = _incident(client)
    assert _draft(client, incident_id, reporters[0][0]).status_code == 403  # customer
    tech_headers, tech = _tech(client)
    assert _draft(client, incident_id, tech_headers).status_code == 403  # not assigned
    tech_id = _query("SELECT staff_id FROM staff WHERE email = ?", (tech["email"],))[0]["staff_id"]
    res = client.put(f"/api/incidents/{incident_id}/assignee",
                     json={"technician_id": tech_id}, headers=_manager(client))
    assert res.status_code == 200, res.text
    assert _draft(client, incident_id, tech_headers).status_code == 200
    # Technicians can't change visibility.
    assert client.put(f"/api/incidents/{incident_id}/visibility", json={"visibility": "public"},
                      headers=tech_headers).status_code == 403


def test_draft_uses_only_supplied_facts(client, model):
    incident_id, _ = _incident(client)
    res = _draft(client, incident_id, _manager(client))
    assert res.status_code == 200, res.text
    cluster = _query("SELECT * FROM incident_clusters WHERE incident_id = ?", (incident_id,))[0]
    summary = cluster["customer_summary"]
    assert cluster["summary_status"] == "draft"
    # Times come from the incident, not the model; no root cause -> under review.
    assert f"From {format_ist(cluster['started_at'])} to {format_ist(cluster['resolved_at'])}." in summary
    assert draft_post_incident.CAUSE_UNDER_REVIEW in summary
    assert GOOD_DRAFT["what_was_fixed"] in summary
    # The model only saw the incident facts and the note — no customer names or emails.
    prompt = json.dumps(model["prompts"][0])
    assert "Restarted the data collectors" in prompt
    assert "Customer Name" not in prompt and "@example.com" not in prompt
    assert _updates(_query("SELECT ticket_id FROM incident_tickets LIMIT 1")[0]["ticket_id"]) == []


def test_staff_root_cause_is_used_verbatim(client, model):
    incident_id, _ = _incident(client)
    _draft(client, incident_id, _manager(client), root_cause="A failed collector config push.")
    summary = _query("SELECT customer_summary FROM incident_clusters")[0]["customer_summary"]
    assert "Cause\nA failed collector config push." in summary


def test_model_inventing_a_time_or_cause_is_rejected(client, model):
    incident_id, _ = _incident(client)
    model["outputs"] = [
        {**GOOD_DRAFT, "what_happened": "Monitors stopped at 3:15 pm yesterday."},
        {**GOOD_DRAFT, "what_was_fixed": "It was caused by a bad deploy, which we rolled back."},
    ]
    res = _draft(client, incident_id, _manager(client))
    assert res.status_code == 200
    assert res.json()["ai_error"]
    cluster = _query("SELECT * FROM incident_clusters")[0]
    assert cluster["summary_status"] == "none" and cluster["customer_summary"] is None
    # The staff input is kept for a manual summary.
    assert cluster["resolution_note"].startswith("Restarted")


def test_ai_failure_never_sends_anything(client):
    """conftest disables the provider: the draft fails, and send refuses."""
    incident_id, reporters = _incident(client)
    manager = _manager(client)
    res = _draft(client, incident_id, manager)
    assert res.json()["ai_error"]
    assert _send(client, incident_id, manager).status_code == 400
    assert all(_updates(t) == [] for _, t in reporters)
    assert _query("SELECT * FROM incident_deliveries") == []
    assert _query("SELECT status FROM incident_clusters")[0]["status"] == "confirmed"


def test_staff_can_write_the_summary_after_an_ai_failure(client):
    incident_id, reporters = _incident(client)
    manager = _manager(client)
    _draft(client, incident_id, manager)
    res = client.put(f"/api/incidents/{incident_id}/resolution/summary",
                     json={"customer_summary": "Monitoring was delayed; it's fixed."}, headers=manager)
    assert res.status_code == 200
    assert _send(client, incident_id, manager).status_code == 200
    assert all(len(_updates(t)) == 1 for _, t in reporters)


def test_send_requires_the_reviewed_text(client, model):
    incident_id, _ = _incident(client)
    manager = _manager(client)
    _draft(client, incident_id, manager)
    res = client.post(f"/api/incidents/{incident_id}/resolution/send",
                      json={"customer_summary": "something else"}, headers=manager)
    assert res.status_code == 409
    assert _query("SELECT * FROM incident_deliveries") == []


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def test_send_fans_out_once_and_is_idempotent(client, model):
    incident_id, reporters = _incident(client)
    manager = _manager(client)
    _draft(client, incident_id, manager)
    res = _send(client, incident_id, manager)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "resolved" and body["summary_status"] == "sent"
    summary = _query("SELECT customer_summary FROM incident_clusters")[0]["customer_summary"]
    for _, ticket_id in reporters:
        (msg,) = _updates(ticket_id)
        assert msg["sender"] == "staff" and msg["author_id"] is None
        assert msg["visibility"] == "public_reply" and msg["message"] == summary
    # Sending again posts nothing new.
    assert _send(client, incident_id, manager).status_code == 200
    assert all(len(_updates(t)) == 1 for _, t in reporters)
    assert {d["status"] for d in _query("SELECT * FROM incident_deliveries")} == {"sent"}
    # Tickets aren't touched unless asked.
    assert {t["status"] for t in _query("SELECT status FROM tickets")} == {"open"}
    actions = _actions(incident_id)
    for action in ("incident.summary_drafted", "incident.summary_approved",
                   "incident.summary_sent", "incident.resolved"):
        assert action in actions
    assert actions.count("incident.resolved") == 1


def test_customer_thread_shows_the_update_from_the_support_team(client, model):
    incident_id, reporters = _resolved_incident(client, model)
    headers, ticket_id = reporters[0]
    thread = client.get(f"/api/tickets/{ticket_id}", headers=headers).json()
    (update,) = [m for m in thread["messages"] if m.get("incident_update")]
    assert update["sender_name"] == "Site24x7 Support Team"
    assert update["subject"] == incident_resolution.UPDATE_SUBJECT


def test_one_failing_ticket_does_not_block_the_rest(client, model, monkeypatch):
    incident_id, reporters = _incident(client)
    manager = _manager(client)
    _draft(client, incident_id, manager)
    bad = reporters[1][1]
    real_post = incident_resolution.post_update

    def _flaky(conn, incident_id_, ticket, text):
        if ticket["ticket_id"] == bad:
            raise RuntimeError("thread unavailable")
        return real_post(conn, incident_id_, ticket, text)

    monkeypatch.setattr(incident_resolution, "post_update", _flaky)
    res = _send(client, incident_id, manager, mark_tickets_resolved=True)
    assert res.status_code == 200
    deliveries = {d["ticket_id"]: d for d in _query("SELECT * FROM incident_deliveries")}
    assert deliveries[bad]["status"] == "failed" and deliveries[bad]["error"] == "RuntimeError"
    assert _updates(bad) == []
    assert _query("SELECT status FROM tickets WHERE ticket_id = ?", (bad,))[0]["status"] == "open"
    for _, ticket_id in (reporters[0], reporters[2]):
        assert deliveries[ticket_id]["status"] == "sent" and len(_updates(ticket_id)) == 1
        assert _query("SELECT status FROM tickets WHERE ticket_id = ?", (ticket_id,))[0]["status"] == "resolved"
    # Retry delivers only the failed one.
    monkeypatch.setattr(incident_resolution, "post_update", real_post)
    assert _send(client, incident_id, manager, mark_tickets_resolved=True).status_code == 200
    assert all(len(_updates(t)) == 1 for _, t in reporters)
    assert {d["status"] for d in _query("SELECT * FROM incident_deliveries")} == {"sent"}


def test_real_email_only_when_enabled(client, model, monkeypatch):
    sent = []
    monkeypatch.setattr(email_utils, "send_email", lambda *a, **k: sent.append(a))
    _resolved_incident(client, model)
    assert sent == []  # EMAIL_ENABLED is off in tests


def test_real_email_sent_once_per_ticket_when_enabled(client, model, monkeypatch):
    sent = []
    monkeypatch.setattr(email_utils, "send_email", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(email_utils, "email_enabled", lambda: True)
    incident_id, _ = _resolved_incident(client, model)
    updates = [s for s in sent if s[1] == incident_resolution.UPDATE_SUBJECT]
    assert len(updates) == 3 and len({s[0] for s in updates}) == 3
    _send(client, incident_id, _manager(client))
    assert len([s for s in sent if s[1] == incident_resolution.UPDATE_SUBJECT]) == 3


# ---------------------------------------------------------------------------
# Memory + answering "did I have an issue?"
# ---------------------------------------------------------------------------
def test_linked_customer_gets_a_yes_with_the_summary(client, model, asks, pipeline):
    incident_id, reporters = _resolved_incident(client, model)
    headers, ticket_id = reporters[0]
    answer = _ask_in_chat(client, headers, pipeline)
    summary = _query("SELECT customer_summary FROM incident_clusters")[0]["customer_summary"]
    assert answer.startswith("Yes — you reported this on")
    assert ticket_id in answer and summary in answer
    # No other reporter's ticket or name leaks.
    for _, other in reporters[1:]:
        assert other not in answer
    assert "Customer Name" not in answer


def test_unlinked_customer_gets_no_details_of_an_affected_only_incident(client, model, asks, pipeline):
    _resolved_incident(client, model)
    headers, _ = _signup(client)
    answer = _ask_in_chat(client, headers, pipeline)
    assert answer == incident_memory.NO_MATCH
    assert "TCK-" not in answer


def test_public_incident_answers_an_unlinked_customer(client, model, asks, pipeline):
    _, reporters = _resolved_incident(client, model, visibility="public")
    headers, _ = _signup(client)
    answer = _ask_in_chat(client, headers, pipeline)
    summary = _query("SELECT customer_summary FROM incident_clusters")[0]["customer_summary"]
    assert answer.startswith("Yes, there was a known issue") and summary in answer
    assert all(t not in answer for _, t in reporters)
    assert "Customer Name" not in answer and "@example.com" not in answer


def test_confirmed_unresolved_incident_is_described_neutrally(client, asks, pipeline):
    _, reporters = _incident(client)
    _exec("UPDATE incident_clusters SET root_cause = 'Secret internal cause'")
    answer = _ask_in_chat(client, reporters[0][0], pipeline)
    assert "investigating" in answer and "Secret internal cause" not in answer


def test_expired_incidents_are_not_used(client, model, asks, pipeline):
    _, reporters = _resolved_incident(client, model, visibility="public")
    old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    _exec("UPDATE incident_memory SET resolved_at = ?", (old,))
    assert incident_memory.public_history(db.get_connection()) == []
    # Even a window that covers the old incident finds nothing.
    answer = _ask_in_chat(client, reporters[0][0], pipeline)
    assert answer == incident_memory.NO_MATCH


def test_memory_days_comes_from_env(client, model, monkeypatch):
    _resolved_incident(client, model, visibility="public")
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    _exec("UPDATE incident_memory SET resolved_at = ?", (old,))
    assert incident_memory.public_history(db.get_connection()) == []
    monkeypatch.setenv("INCIDENT_MEMORY_DAYS", "120")
    app_settings.clear_cache()
    assert incident_memory.memory_days() == 120
    assert len(incident_memory.public_history(db.get_connection())) == 1


def test_dismissed_incident_is_forgotten(client):
    _, _ = _incident(client)
    assert len(_query("SELECT * FROM incident_memory")) == 1
    (cluster,) = _query("SELECT incident_id FROM incident_clusters")
    _exec("UPDATE incident_clusters SET status = 'detected'")
    client.post(f"/api/incidents/{cluster['incident_id']}/review",
                json={"action": "dismiss", "note": "False alarm"}, headers=_admin(client))
    assert _query("SELECT * FROM incident_memory") == []


def test_ticket_reply_answers_from_memory(client, model, asks, pipeline):
    _resolved_incident(client, model)
    headers, _ = _signup(client)
    pipeline.route = "ai"
    ticket_id = client.post("/api/tickets", json={
        "subject": "Adding a monitor", "description": "How do I add a monitor?"}, headers=headers
    ).json()["ticket_id"]
    res = client.post(f"/api/tickets/{ticket_id}/reply", json={"message": "Is this a known issue?"},
                      headers=headers)
    assert res.status_code == 200, res.text
    last_ai = _query("SELECT message FROM ticket_messages WHERE ticket_id = ? AND sender = 'ai' "
                     "ORDER BY rowid DESC LIMIT 1", (ticket_id,))[0]["message"]
    assert incident_memory.NO_MATCH_ON_TICKET in last_ai


def test_ordinary_messages_never_reach_the_incident_parser(client, asks, pipeline):
    headers, _ = _signup(client)
    _ask_in_chat(client, headers, pipeline, question="How do I set an alert threshold?")
    assert asks == []


def test_parser_failure_leaves_the_chat_unchanged(client, monkeypatch, pipeline):
    from backend.ai_service import AIError

    def _fail(data):
        raise AIError("down")

    monkeypatch.setattr(incident_question, "parse_incident_question", _fail)
    headers, _ = _signup(client)
    pipeline.route = "ai"
    chat = client.post("/api/chats", json={"issue": "How do I add a monitor?"}, headers=headers).json()
    res = client.post(f"/api/chats/{chat['session_id']}/messages",
                      json={"message": "Was there an outage today?"}, headers=headers).json()
    assert res["ai_error"]
    assert res["status"] == "active"


def test_public_history_needs_no_login_and_shows_only_public(client, model):
    _resolved_incident(client, model)
    assert client.get("/api/public/incidents").json() == []
    (cluster,) = _query("SELECT incident_id FROM incident_clusters")
    res = client.put(f"/api/incidents/{cluster['incident_id']}/visibility",
                     json={"visibility": "public"}, headers=_manager(client))
    assert res.status_code == 200
    (item,) = client.get("/api/public/incidents").json()
    assert set(item) == {"reference", "status", "category", "started_at", "resolved_at", "summary"}
    assert item["status"] == "resolved"
    assert "visibility_changed" in " ".join(_actions(cluster["incident_id"]))
