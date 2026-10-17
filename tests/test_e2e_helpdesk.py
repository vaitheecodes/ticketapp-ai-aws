"""End-to-end: the whole help-desk journey through the real app.

Customer chats with the AI -> the AI can't resolve it -> the chat escalates
to an unassigned ticket with an AI summary and suggested steps -> a
technician claims it, replies publicly and adds a private note -> the
customer sees the reply but not the note -> other customers report the
same problem -> the manager sees the correlated incident and confirms it
-> the first customer sees the neutral incident notice.

Everything is real (FastAPI app, LangGraph routing pipeline, SQLite,
background correlation, permissions) except the language model:
provider.complete is replaced by a scripted fake that answers each AI
operation the way a well-behaved model would. Only the model is mocked.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from backend import db, graph, main, runtime, seed_data
from backend.ai_service import provider

PRIVATE_NOTE = "Internal: three EU customers on agent 19.4 — check the eu-west collector first."


class _ScriptedModel:
    """Answers by operation, recognised from the trusted instructions."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, messages, **kwargs):
        system, user = messages[0]["content"], messages[1]["content"]
        if "You classify support tickets" in system:
            self.calls.append("categorize")
            return json.dumps({"category": "SERVER_MONITORING", "confidence": 0.92})
        if "helpful customer support agent" in system:
            self.calls.append("answer")
            return "Please restart the Site24x7 agent service and check that it can reach the data collector."
        if "Decide whether the customer's new message is a CONTINUATION" in system:
            self.calls.append("continuation")
            return json.dumps({"is_continuation": False})
        if "taking over a chat" in system:
            self.calls.append("summarize_escalation")
            return json.dumps({
                "issue": "Server monitors stopped collecting data at 10:00 UTC.",
                "tried": ["Restarted the agent service."],
                "findings": ["Linux servers in eu-west", "Agent 19.4"],
                "unresolved_questions": ["Can the agent reach the data collector on 443?"],
            })
        if "Suggest 2-5 concrete next steps" in system:
            self.calls.append("suggest_steps")
            return json.dumps({"steps": ["Check agent logs for collector timeouts.",
                                         "Confirm outbound 443 to the eu-west collector."]})
        if "spot incidents" in system:
            self.calls.append("correlate")
            ids = re.findall(r"ticket_id: (\S+)", user)
            return json.dumps({
                "is_related": True, "related_ticket_ids": ids, "title": "Server monitors not collecting data",
                "why_related": "Same symptom from different customers within minutes.",
                "observed_facts": ["Several customers report no server data since about 10:00 UTC"],
                "hypotheses": ["A data collector problem in one region"],
                "confidence": 0.82, "confidence_reasons": ["Same component", "Same time window"],
                "suggested_next_steps": ["Check collector health"], "candidate_departments": ["Infrastructure"],
            })
        if "concise summary of a possible Site24x7 incident" in system:
            self.calls.append("summarize_incident")
            return json.dumps({"title": "Server monitors not collecting data",
                               "summary": "Several customers report server monitors with no data since 10:00 UTC."})
        raise AssertionError(f"Unexpected AI operation: {system[:80]}")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    model = _ScriptedModel()
    monkeypatch.setattr(provider, "complete", model)
    with TestClient(main.app) as c:
        runtime.set_graph(graph.build_graph())  # the real routing pipeline
        c.model = model
        yield c


def _bearer(res):
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _signup(client, name):
    return _bearer(client.post("/api/auth/customer/signup", json={
        "name": name, "email": f"{name.split()[0].lower()}@e2e.example.com", "password": "e2e-password"}))


def _staff(client, role):
    (p,) = [p for p in seed_data.DEMO_STAFF if p["role"] == role]
    return _bearer(client.post("/api/auth/login", json={"email": p["email"], "password": p["password"]}))


def test_customer_to_technician_to_manager_journey(client):
    # --- 1. Customer chats with the AI, which answers -----------------------
    ana = _signup(client, "Ana Ruiz")
    chat = client.post("/api/chats", json={"issue": "My server monitors stopped collecting data at 10:00 UTC"},
                       headers=ana).json()
    assert chat["status"] == "active"
    assert chat["messages"][-1]["content"].startswith("Please restart the Site24x7 agent")

    # --- 2. The AI's answer doesn't fix it -> escalation --------------------
    chat = client.post(f"/api/chats/{chat['session_id']}/messages",
                       json={"message": "I restarted it but it's still not working"}, headers=ana).json()
    assert chat["status"] == "escalated"
    ticket_id = chat["ticket_id"]
    assert ticket_id in chat["messages"][-1]["content"]          # customer told the reference
    assert "won't need to repeat" in chat["messages"][-1]["content"]

    # --- 3. Unassigned queue, with the AI hand-off for the technician -------
    tech = _staff(client, "technician")
    queue = client.get("/api/staff/tickets?view=unassigned", headers=tech).json()
    (row,) = [t for t in queue if t["ticket_id"] == ticket_id]
    assert row["assignee_name"] is None and row["status"] == "open"
    workspace = client.get(f"/api/staff/tickets/{ticket_id}", headers=tech).json()
    assert workspace["ai"]["summary"]["issue"] == "Server monitors stopped collecting data at 10:00 UTC."
    assert workspace["ai"]["suggestions"][0] == "Check agent logs for collector timeouts."
    assert [m["role"] for m in workspace["chat"]["transcript"]] == ["customer", "ai", "customer", "ai"]

    # --- 4. Technician claims, replies publicly, adds a private note --------
    assert client.post(f"/api/staff/tickets/{ticket_id}/claim", headers=tech).status_code == 200
    reply = client.post(f"/api/staff/tickets/{ticket_id}/messages", headers=tech, json={
        "body": "Thanks Ana — could you send the last 50 lines of the agent log?",
        "visibility": "public_reply", "set_status": "pending_customer"})
    assert reply.status_code == 200, reply.text
    note = client.post(f"/api/staff/tickets/{ticket_id}/messages", headers=tech,
                       json={"body": PRIVATE_NOTE, "visibility": "internal_note"})
    assert note.status_code == 200

    # --- 5. Customer sees the reply, never the note or the AI internals -----
    detail = client.get(f"/api/tickets/{ticket_id}", headers=ana)
    body = detail.text
    messages = detail.json()["messages"]
    assert any(m["sender"] == "staff" and "agent log" in m["message"] for m in messages)
    assert PRIVATE_NOTE not in body
    assert "Check agent logs for collector timeouts" not in body
    assert detail.json()["ticket"]["status"] == "pending_customer"
    reply = client.post(f"/api/tickets/{ticket_id}/reply", json={"message": "Log attached shortly."}, headers=ana)
    assert reply.json()["status"] == "open"                      # back with the support team

    # --- 6. Other customers report the same problem -------------------------
    for name in ("Ben Okafor", "Chen Li"):
        res = client.post("/api/tickets", headers=_signup(client, name), json={
            "subject": "Server monitors not collecting data",
            "description": "Our servers have had no data since 10:00 UTC, the agent is not uploading.",
            "priority": "high"})
        assert res.status_code == 200 and res.json()["status"] == "open"
    assert "correlate" in client.model.calls                     # background correlation ran

    # --- 7. Manager sees the correlated incident and confirms it ------------
    manager = _staff(client, "manager")                          # Infrastructure department
    (incident,) = client.get("/api/incidents", headers=manager).json()
    assert incident["ticket_count"] == 3 and incident["customer_count"] == 3
    assert [d["name"] for d in incident["departments"]] == ["Infrastructure"]
    detail = client.get(f"/api/incidents/{incident['incident_id']}", headers=manager).json()
    assert ticket_id in {t["ticket_id"] for t in detail["tickets"]}
    assert detail["summary"]["observed_facts"] and detail["summary"]["hypotheses"]
    assert client.get(f"/api/staff/tickets/{ticket_id}", headers=manager).status_code == 200
    confirmed = client.post(f"/api/incidents/{incident['incident_id']}/review", headers=manager,
                            json={"action": "confirm", "note": "Collector issue confirmed with the platform team."})
    assert confirmed.json()["status"] == "confirmed"

    # --- 8. Customer sees a neutral notice; nothing was closed or merged ----
    detail = client.get(f"/api/tickets/{ticket_id}", headers=ana).json()
    assert detail["incident_notice"].startswith("We're aware of this issue")
    assert detail["ticket"]["status"] == "open"

    # --- 9. It's all on the record ------------------------------------------
    admin = _bearer(client.post("/api/auth/login", json={"email": "admin@example.com",
                                                         "password": "admin-dev-password"}))
    actions = {e["action"] for e in client.get("/api/admin/audit", params={"limit": 200},
                                               headers=admin).json()["events"]}
    assert {"ticket.claimed", "ticket.status_changed", "ticket.assignment_changed", "incident.confirm"} <= actions
