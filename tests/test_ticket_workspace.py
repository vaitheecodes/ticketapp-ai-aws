"""Sub-stage B: ticket lifecycle, claiming, message visibility, history,
and the technician/manager workspace (backend/staff_tickets.py).

Real temporary SQLite DB via FastAPI's TestClient; the pipeline is a stub
that routes every new ticket to a human, so tickets start unassigned and
Open. No LLM calls.
"""

from __future__ import annotations

import itertools
import json
import threading

import pytest
from fastapi.testclient import TestClient

from backend import db, email_utils, main, runtime, seed_data

_counter = itertools.count()
_HUMAN = {"route": "human", "category": "SERVER_MONITORING", "confidence": 0.4, "clarification_attempts": 0}
NOTE = "Internal: customer is on the legacy plan, check with billing first."


class _AlwaysHuman:
    def invoke(self, state):
        return dict(_HUMAN)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with TestClient(main.app) as c:
        runtime.set_graph(_AlwaysHuman())
        yield c


@pytest.fixture()
def sent_emails(monkeypatch):
    # Real email is off unless explicitly enabled and configured.
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.test.invalid")
    monkeypatch.setenv("SMTP_FROM", "support@test.invalid")
    sent = []
    monkeypatch.setattr(email_utils, "send_email", lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _customer(client):
    res = client.post("/api/auth/customer/signup", json={
        "name": "Dana Smith", "email": f"ws-{next(_counter)}@example.com", "password": "password123"})
    return _bearer(res.json()["token"])


def _login(client, email, password):
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return _bearer(res.json()["token"])


def _demo(client, role):
    (p,) = [p for p in seed_data.DEMO_STAFF if p["role"] == role]
    return _login(client, p["email"], p["password"])


def _admin(client):
    return _login(client, "admin@example.com", "admin-dev-password")


def _new_technician(client):
    email = f"tech-{next(_counter)}@example.com"
    res = client.post("/api/admin/staff", json={
        "name": f"Tech {email}", "email": email, "role": "technician", "temporary_password": "temp-pass-123"},
        headers=_admin(client))
    assert res.status_code == 200, res.text
    headers = _login(client, email, "temp-pass-123")
    client.post("/api/auth/change-password",
                json={"current_password": "temp-pass-123", "new_password": "real-pass-456"}, headers=headers)
    return headers, res.json()["staff_id"]


def _raise(client, customer, subject="Website monitor shows down", category="SERVER_MONITORING",
           expected_status="open"):
    res = client.post("/api/tickets", json={
        "subject": subject, "description": "Our site monitor says down but the site is up.",
        "priority": "high", "category": category}, headers=customer)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == expected_status
    return res.json()["ticket_id"]


def _claim(client, tech, ticket_id):
    return client.post(f"/api/staff/tickets/{ticket_id}/claim", headers=tech)


def _post(client, tech, ticket_id, body, visibility="public_reply", **extra):
    return client.post(f"/api/staff/tickets/{ticket_id}/messages",
                       json={"body": body, "visibility": visibility, **extra}, headers=tech)


def _query(sql, params=()):
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------
def test_claim_makes_technician_owner_and_moves_to_in_progress(client):
    ticket_id = _raise(client, _customer(client))
    tech = _demo(client, "technician")
    res = _claim(client, tech, ticket_id)
    assert res.status_code == 200, res.text
    ws = res.json()
    assert ws["ticket"]["status"] == "in_progress"
    assert ws["ticket"]["assignee"]["name"] == "Demo Technician"
    assert ws["can"] == {"claim": False, "reply": True, "change_status": True, "note": True}

    unassigned = client.get("/api/staff/tickets?view=unassigned", headers=tech).json()
    assert ticket_id not in [t["ticket_id"] for t in unassigned]
    mine = client.get("/api/staff/tickets?view=mine", headers=tech).json()
    assert [t["ticket_id"] for t in mine] == [ticket_id]


def test_second_claim_gets_409(client):
    ticket_id = _raise(client, _customer(client))
    assert _claim(client, _demo(client, "technician"), ticket_id).status_code == 200
    other, _ = _new_technician(client)
    res = _claim(client, other, ticket_id)
    assert res.status_code == 409


def test_simultaneous_claims_exactly_one_wins(client):
    ticket_id = _raise(client, _customer(client))
    techs = [_demo(client, "technician"), _new_technician(client)[0]]
    barrier = threading.Barrier(len(techs))
    results = []

    def claim(headers):
        barrier.wait()
        results.append(_claim(client, headers, ticket_id).status_code)

    threads = [threading.Thread(target=claim, args=(h,)) for h in techs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409]
    assert len(_query("SELECT * FROM ticket_assignment_history WHERE ticket_id = ?", (ticket_id,))) == 1


def test_only_technicians_claim(client):
    ticket_id = _raise(client, _customer(client))
    assert _claim(client, _demo(client, "manager"), ticket_id).status_code == 403
    assert _claim(client, _customer(client), ticket_id).status_code == 403


# ---------------------------------------------------------------------------
# Public replies vs internal notes
# ---------------------------------------------------------------------------
def test_public_reply_reaches_the_customer_thread(client, sent_emails):
    customer = _customer(client)
    ticket_id = _raise(client, customer)
    tech = _demo(client, "technician")
    _claim(client, tech, ticket_id)
    res = _post(client, tech, ticket_id, "Please check the monitor's location settings.",
                subject="Your website monitor", set_status="pending_customer")
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/tickets/{ticket_id}", headers=customer).json()
    reply = [m for m in detail["messages"] if m["sender"] == "staff"]
    assert len(reply) == 1
    assert reply[0]["sender_name"] == "Demo Technician"
    assert reply[0]["sender_role"] == "Support Agent"
    assert reply[0]["subject"] == "Your website monitor"
    assert detail["ticket"]["status"] == "pending_customer"
    assert client.get("/api/tickets/mine", headers=customer).json()[0]["unread"] is False  # just read
    assert sent_emails and sent_emails[-1][1] == "Your website monitor"


def test_internal_note_is_never_returned_by_customer_endpoints(client):
    customer = _customer(client)
    ticket_id = _raise(client, customer)
    tech = _demo(client, "technician")
    # Any technician can add a note, owner or not.
    res = _post(client, tech, ticket_id, NOTE, visibility="internal_note")
    assert res.status_code == 200, res.text
    assert any(m["visibility"] == "internal_note" for m in res.json()["messages"])

    for url in (f"/api/tickets/{ticket_id}", "/api/tickets/mine", "/api/chats"):
        body = client.get(url, headers=customer).text
        assert NOTE not in body, url
    # A note doesn't light up the customer's unread dot either.
    (item,) = client.get("/api/tickets/mine", headers=customer).json()
    assert item["unread"] is False


def test_internal_notes_never_reach_the_ai_transcript(client):
    customer = _customer(client)
    ticket_id = _raise(client, customer)
    _post(client, _demo(client, "technician"), ticket_id, NOTE, visibility="internal_note")
    conn = db.get_connection()
    try:
        assert NOTE not in main._transcript_for(conn, ticket_id)
    finally:
        conn.close()


def test_only_the_owner_replies_or_changes_status(client):
    ticket_id = _raise(client, _customer(client))
    owner = _demo(client, "technician")
    other, _ = _new_technician(client)

    assert _post(client, owner, ticket_id, "hi").status_code == 403  # must claim first
    _claim(client, owner, ticket_id)
    assert _post(client, other, ticket_id, "hi").status_code == 403
    assert client.post(f"/api/staff/tickets/{ticket_id}/status", json={"status": "resolved"},
                       headers=other).status_code == 403
    assert _post(client, other, ticket_id, "note", visibility="internal_note").status_code == 200
    assert client.post(f"/api/staff/tickets/{ticket_id}/status", json={"status": "bogus"},
                       headers=owner).status_code == 422


# ---------------------------------------------------------------------------
# Lifecycle + history
# ---------------------------------------------------------------------------
def test_status_and_assignment_history_is_written(client):
    customer = _customer(client)
    ticket_id = _raise(client, customer)
    tech = _demo(client, "technician")
    _claim(client, tech, ticket_id)
    client.post(f"/api/staff/tickets/{ticket_id}/status", json={"status": "resolved"}, headers=tech)
    client.post(f"/api/tickets/{ticket_id}/reply", json={"message": "Still broken."}, headers=customer)

    transitions = [(r["from_status"], r["to_status"]) for r in _query(
        "SELECT * FROM ticket_status_history WHERE ticket_id = ? ORDER BY id", (ticket_id,))]
    assert transitions == [(None, "open"), ("open", "in_progress"), ("in_progress", "resolved"),
                           ("resolved", "open")]
    (assignment,) = _query("SELECT * FROM ticket_assignment_history WHERE ticket_id = ?", (ticket_id,))
    assert assignment["from_user"] is None and assignment["to_user"].startswith("TECH-")

    ws = client.get(f"/api/staff/tickets/{ticket_id}", headers=tech).json()
    assert ws["ticket"]["status"] == "open"
    assert ws["ticket"]["assignee"]["name"] == "Demo Technician"  # owner kept on reopen
    assert ws["status_history"][-1]["changed_by_name"] == "Dana Smith"


def test_customer_reply_returns_pending_customer_to_open(client):
    customer = _customer(client)
    ticket_id = _raise(client, customer)
    tech = _demo(client, "technician")
    _claim(client, tech, ticket_id)
    _post(client, tech, ticket_id, "Which monitor?", set_status="pending_customer")
    res = client.post(f"/api/tickets/{ticket_id}/reply", json={"message": "The EU one."}, headers=customer)
    assert res.json()["status"] == "open"


def test_status_change_events_show_to_customer_but_admin_reassign_does_not(client):
    customer = _customer(client)
    ticket_id = _raise(client, customer)
    tech = _demo(client, "technician")
    _claim(client, tech, ticket_id)
    client.post(f"/api/staff/tickets/{ticket_id}/status", json={"status": "resolved"}, headers=tech)
    _, other_id = _new_technician(client)
    res = client.post(f"/api/admin/tickets/{ticket_id}/reassign", json={"technician_id": other_id},
                      headers=_admin(client))
    assert res.status_code == 200

    events = [m["message"] for m in client.get(f"/api/tickets/{ticket_id}", headers=customer).json()["messages"]
              if m["sender"] == "system"]
    assert "Demo Technician is now handling your ticket." in events
    assert "Status changed to Resolved." in events
    assert not any("administrator" in e for e in events)

    history = _query("SELECT * FROM ticket_assignment_history WHERE ticket_id = ? ORDER BY id", (ticket_id,))
    assert history[-1]["to_user"] == other_id and history[-1]["changed_by"].startswith("ADMIN-")


def test_legacy_system_messages_become_customer_visible_events(client):
    customer = _customer(client)
    ticket_id = _raise(client, customer)
    conn = db.get_connection()
    conn.execute("UPDATE ticket_messages SET visibility = 'public_reply' WHERE ticket_id = ?", (ticket_id,))
    conn.execute(
        "INSERT INTO ticket_messages (message_id, ticket_id, sender, message, created_at) "
        "VALUES ('MSG-legacy', ?, 'system', 'Old note', '2026-01-01T00:00:00+00:00')", (ticket_id,))
    conn.commit()
    conn.close()
    db.init_db()
    (row,) = _query("SELECT visibility, customer_visible FROM ticket_messages WHERE message_id = 'MSG-legacy'")
    assert row == {"visibility": "system_event", "customer_visible": 1}


# ---------------------------------------------------------------------------
# Workspace views, AI panel, scope
# ---------------------------------------------------------------------------
def test_views_search_and_filters(client):
    customer = _customer(client)
    a = _raise(client, customer, subject="SSL alert spam")
    b = _raise(client, customer, subject="Agent offline")
    tech = _demo(client, "technician")
    _claim(client, tech, b)

    ids = lambda url: [t["ticket_id"] for t in client.get(url, headers=tech).json()]  # noqa: E731
    assert ids("/api/staff/tickets?view=unassigned") == [a]
    assert set(ids("/api/staff/tickets?view=open_pending")) == {a, b}
    assert ids("/api/staff/tickets?view=recent&q=ssl") == [a]
    assert ids("/api/staff/tickets?view=recent&status=in_progress") == [b]
    assert ids("/api/staff/tickets?view=recent&priority=low") == []
    row = client.get("/api/staff/tickets?view=mine", headers=tech).json()[0]
    assert row["customer_name"] == "Dana Smith" and row["priority"] == "high"


def test_ai_handled_tickets_are_not_in_the_technician_workspace(client):
    customer = _customer(client)
    runtime.set_graph(type("G", (), {"invoke": lambda self, s: {
        "route": "ai", "category": "AGENT", "confidence": 0.95, "ai_response": "Do X.",
        "clarification_attempts": 0}})())
    ticket_id = _raise(client, customer, expected_status="resolved")
    tech = _demo(client, "technician")
    assert client.get(f"/api/staff/tickets/{ticket_id}", headers=tech).status_code == 403
    assert ticket_id not in [t["ticket_id"] for t in client.get("/api/staff/tickets?view=recent", headers=tech).json()]


def test_escalated_chat_shows_ai_panel_and_transcript_to_technician(client, monkeypatch):
    from backend.ai_service import suggest_steps, summarize_conversation
    from backend.ai_service.schemas import HandoffSummary, SuggestedSteps

    brief = HandoffSummary(issue="Monitor down")
    monkeypatch.setattr(summarize_conversation, "summarize_escalation", lambda t, r: brief)
    monkeypatch.setattr(suggest_steps, "suggest_steps",
                        lambda data: SuggestedSteps(steps=["Check probe locations."]))
    customer = _customer(client)
    chat = client.post("/api/chats", json={"issue": "my monitor is down"}, headers=customer).json()
    ws = client.get(f"/api/staff/tickets/{chat['ticket_id']}", headers=_demo(client, "technician")).json()
    assert ws["ai"]["summary"]["issue"] == "Monitor down"
    assert ws["ai"]["suggestions"] == ["Check probe locations."]
    assert ws["chat"]["transcript"][0]["content"] == "my monitor is down"


def test_manager_oversees_own_departments_read_only(client):
    customer = _customer(client)
    # Demo manager's department is Infrastructure; NETFLOW is Network.
    in_scope = _raise(client, customer, category="SERVER_MONITORING")
    runtime.set_graph(type("G", (), {"invoke": lambda self, s: {**_HUMAN, "category": "NETFLOW"}})())
    out_of_scope = _raise(client, customer, category="NETFLOW")
    tech = _demo(client, "technician")
    _claim(client, tech, in_scope)
    manager = _demo(client, "manager")

    overview = client.get("/api/manager/overview", headers=manager).json()
    assert [t["ticket_id"] for t in overview["tickets"]] == [in_scope]
    (load,) = [w for w in overview["workload"] if w["name"] == "Demo Technician"]
    assert load["in_progress"] == 1
    assert client.get(f"/api/staff/tickets/{in_scope}", headers=manager).json()["can"] == {
        "claim": False, "reply": False, "change_status": False, "note": False}
    assert client.get(f"/api/staff/tickets/{out_of_scope}", headers=manager).status_code == 403
    assert _post(client, manager, in_scope, "hi", visibility="internal_note").status_code == 403
    assert client.get("/api/manager/overview", headers=tech).status_code == 403


def test_customers_cannot_use_staff_endpoints(client):
    customer = _customer(client)
    ticket_id = _raise(client, customer)
    assert client.get(f"/api/staff/tickets/{ticket_id}", headers=customer).status_code == 403
    assert client.get("/api/staff/tickets", headers=customer).status_code == 403
    assert _post(client, customer, ticket_id, "x", visibility="internal_note").status_code == 403
    assert json.loads(client.get(f"/api/tickets/{ticket_id}", headers=_customer(client)).text)[
        "detail"] == "Not your ticket"
