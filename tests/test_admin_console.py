"""Sub-stage E: admin console (backend/admin.py), one department per
manager, application settings, the audit log, email off by default, and
attachment access.

Real temporary SQLite DB via FastAPI's TestClient; the routing pipeline is
a stub that sends every ticket to a human. No LLM calls.
"""

from __future__ import annotations

import itertools
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import db, email_utils, graph, incidents, main, runtime, seed_data

_counter = itertools.count()
TEMP_PASSWORD = "temp-secret-pass-123"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class _Human:
    def invoke(self, state):
        return {"route": "human", "category": "SERVER_MONITORING", "confidence": 0.5,
                "clarification_attempts": 0}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with TestClient(main.app) as c:
        runtime.set_graph(_Human())
        yield c


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _admin(client):
    return _bearer(_login(client, "admin@example.com", "admin-dev-password").json()["token"])


def _demo(client, role):
    (p,) = [p for p in seed_data.DEMO_STAFF if p["role"] == role]
    return _bearer(_login(client, p["email"], p["password"]).json()["token"])


def _signup(client):
    email = f"cust-{next(_counter)}@example.com"
    res = client.post("/api/auth/customer/signup", json={"name": "Dana", "email": email, "password": "password123"})
    return _bearer(res.json()["token"]), email


def _departments(client):
    return {d["name"]: d["department_id"]
            for d in client.get("/api/admin/departments", headers=_admin(client)).json()["departments"]}


def _audit(client, **filters):
    res = client.get("/api/admin/audit", params=filters, headers=_admin(client))
    assert res.status_code == 200, res.text
    return res.json()["events"]


def _raise(client, headers):
    res = client.post("/api/tickets", json={"subject": "Agent offline", "description": "No data since 10:00"},
                      headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# Customer accounts
# ---------------------------------------------------------------------------
def test_admin_creates_customer_who_must_change_password(client):
    res = client.post("/api/admin/customers", json={
        "name": "Erin Admin-Made", "email": "erin@example.com", "temporary_password": TEMP_PASSWORD},
        headers=_admin(client))
    assert res.status_code == 200, res.text
    assert res.json()["must_change_password"] is True

    login = _login(client, "erin@example.com", TEMP_PASSWORD).json()
    assert login["role"] == "customer" and login["must_change_password"] is True
    headers = _bearer(login["token"])
    assert client.get("/api/tickets/mine", headers=headers).status_code == 403
    client.post("/api/auth/change-password",
                json={"current_password": TEMP_PASSWORD, "new_password": "erin-own-pass"}, headers=headers)
    assert client.get("/api/tickets/mine", headers=headers).status_code == 200


def test_admin_updates_deactivates_and_resets_customers(client):
    headers, email = _signup(client)
    admin = _admin(client)
    (row,) = [c for c in client.get("/api/admin/customers", params={"q": email}, headers=admin).json()]
    url = f"/api/admin/customers/{row['customer_id']}"

    assert client.patch(url, json={"name": "Dana Renamed"}, headers=admin).json()["name"] == "Dana Renamed"
    assert client.patch(url, json={"email": "admin@example.com"}, headers=admin).status_code == 400

    client.post(f"{url}/deactivate", headers=admin)
    assert client.get("/api/tickets/mine", headers=headers).status_code == 403  # live session blocked
    assert _login(client, email, "password123").status_code == 403
    client.post(f"{url}/activate", headers=admin)
    assert _login(client, email, "password123").status_code == 200

    client.post(f"{url}/reset-password", json={"temporary_password": TEMP_PASSWORD}, headers=admin)
    assert _login(client, email, TEMP_PASSWORD).json()["must_change_password"] is True


def test_only_admin_manages_accounts(client):
    for headers in (_signup(client)[0], _demo(client, "technician"), _demo(client, "manager")):
        assert client.get("/api/admin/customers", headers=headers).status_code == 403
        assert client.get("/api/admin/audit", headers=headers).status_code == 403
        assert client.get("/api/admin/settings", headers=headers).status_code == 403


# ---------------------------------------------------------------------------
# Managers: role + one department
# ---------------------------------------------------------------------------
def test_admin_creates_manager_with_one_department(client):
    depts = _departments(client)
    res = client.post("/api/admin/staff", json={
        "name": "Nia Network", "email": "nia@example.com", "role": "manager",
        "temporary_password": TEMP_PASSWORD, "department_id": depts["Network"]}, headers=_admin(client))
    assert res.status_code == 200, res.text
    assert res.json()["department_name"] == "Network"

    headers = _bearer(_login(client, "nia@example.com", TEMP_PASSWORD).json()["token"])
    client.post("/api/auth/change-password",
                json={"current_password": TEMP_PASSWORD, "new_password": "nia-own-pass"}, headers=headers)
    assert [d["name"] for d in client.get("/api/manager/departments", headers=headers).json()] == ["Network"]


def test_admin_assigns_manager_role_and_department(client):
    admin = _admin(client)
    tech = client.post("/api/admin/staff", json={
        "name": "Tom", "email": "tom@example.com", "role": "technician", "temporary_password": TEMP_PASSWORD},
        headers=admin).json()
    token = _login(client, "tom@example.com", TEMP_PASSWORD).json()["token"]

    res = client.patch(f"/api/admin/staff/{tech['staff_id']}",
                       json={"role": "manager", "department_id": _departments(client)["Network"]}, headers=admin)
    assert res.status_code == 200, res.text
    assert (res.json()["role"], res.json()["department_name"]) == ("manager", "Network")
    # The old technician token no longer works.
    assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 401

    back = client.patch(f"/api/admin/staff/{tech['staff_id']}", json={"role": "technician"}, headers=admin).json()
    assert back["department_id"] is None  # technicians have no department


def test_technician_with_open_tickets_cannot_change_role(client):
    customer, _ = _signup(client)
    ticket_id = _raise(client, customer)["ticket_id"]
    tech = _demo(client, "technician")
    client.post(f"/api/staff/tickets/{ticket_id}/claim", headers=tech)
    staff = client.get("/api/admin/staff", headers=_admin(client)).json()
    (row,) = [s for s in staff if s["role"] == "technician"]
    res = client.patch(f"/api/admin/staff/{row['staff_id']}", json={"role": "manager"}, headers=_admin(client))
    assert res.status_code == 409


def test_legacy_manager_mapping_keeps_the_first_department(client):
    staff = client.get("/api/admin/staff", headers=_admin(client)).json()
    (manager,) = [s for s in staff if s["role"] == "manager"]
    depts = _departments(client)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("UPDATE staff SET department_id = NULL WHERE staff_id = ?", (manager["staff_id"],))
    conn.executemany("INSERT INTO manager_departments (manager_id, department_id) VALUES (?, ?)",
                     [(manager["staff_id"], depts["Network"]), (manager["staff_id"], depts["Integrations"])])
    conn.commit()
    conn.close()
    db.init_db()
    (after,) = [s for s in client.get("/api/admin/staff", headers=_admin(client)).json()
                if s["staff_id"] == manager["staff_id"]]
    assert after["department_name"] == "Network"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def test_settings_are_validated(client):
    res = client.put("/api/admin/settings/routing", json={
        "confidence_threshold": 0.9, "high_risk_confidence_threshold": 0.5}, headers=_admin(client))
    assert res.status_code == 422
    assert "high-risk bar" in res.json()["detail"]
    res = client.put("/api/admin/settings/routing", json={"high_risk_categories": ["REFUNDS"]},
                     headers=_admin(client))
    assert res.status_code == 422


def test_routing_settings_apply_to_the_live_policy(client):
    admin = _admin(client)
    res = client.put("/api/admin/settings/routing", json={
        "confidence_threshold": 0.6, "high_risk_confidence_threshold": 0.95,
        "high_risk_categories": ["ACCOUNT", "DATA", "ALERTS"],
        "escalation_language_tags": ["offensive_language"], "max_clarification_attempts": 1}, headers=admin)
    assert res.status_code == 200, res.text
    policy = client.get("/api/debug/routing-policy").json()
    assert policy["high_risk_categories"] == ["ACCOUNT", "ALERTS", "DATA"]
    assert policy["max_clarification_attempts"] == 1
    routed = graph.decide_route({"category": "AGENT", "confidence": 0.65, "language_tags": ["negation"],
                                 "clarification_attempts": 0})
    assert routed["route"] == "ai"  # 0.65 clears the new 0.6 bar; negation no longer forces a human

    client.delete("/api/admin/settings/routing", headers=admin)
    assert client.get("/api/debug/routing-policy").json()["confidence_threshold"] == 0.75


def test_incident_settings_override_env(client, monkeypatch):
    monkeypatch.setenv("INCIDENT_MIN_TICKETS", "4")
    assert incidents.settings()["min_tickets"] == 4
    client.put("/api/admin/settings/incidents", json={"window_hours": 24, "min_tickets": 2,
                                                       "similarity": 0.8, "routing_confidence": 0.5},
               headers=_admin(client))
    assert incidents.settings()["min_tickets"] == 2
    assert client.get("/api/admin/settings", headers=_admin(client)).json()["defaults"]["incidents"][
        "min_tickets"] == 4


def test_status_labels_are_configurable(client):
    client.put("/api/admin/settings/statuses", json={"resolved": "Solved", "pending_customer": "Waiting on you"},
               headers=_admin(client))
    assert client.get("/api/meta").json()["status_labels"]["resolved"] == "Solved"

    customer, _ = _signup(client)
    ticket_id = _raise(client, customer)["ticket_id"]
    tech = _demo(client, "technician")
    client.post(f"/api/staff/tickets/{ticket_id}/claim", headers=tech)
    client.post(f"/api/staff/tickets/{ticket_id}/status", json={"status": "resolved"}, headers=tech)
    events = [m["message"] for m in client.get(f"/api/tickets/{ticket_id}", headers=customer).json()["messages"]
              if m["sender"] == "system"]
    assert "Status changed to Solved." in events


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def test_audit_records_ticket_work(client):
    customer, _ = _signup(client)
    ticket_id = _raise(client, customer)["ticket_id"]
    tech = _demo(client, "technician")
    client.post(f"/api/staff/tickets/{ticket_id}/claim", headers=tech)
    client.post(f"/api/staff/tickets/{ticket_id}/status", json={"status": "pending_customer"}, headers=tech)

    actions = [e["action"] for e in _audit(client, entity="ticket", entity_id=ticket_id)]
    assert "ticket.claimed" in actions
    assert "ticket.assignment_changed" in actions
    assert actions.count("ticket.status_changed") == 2  # open->in_progress, ->pending_customer
    claimed = _audit(client, action="ticket.claimed")[0]
    assert claimed["actor_name"] == "Demo Technician"


def test_audit_records_admin_changes_without_secrets(client):
    admin = _admin(client)
    staff = client.post("/api/admin/staff", json={
        "name": "Audit Me", "email": "audit-me@example.com", "role": "technician",
        "temporary_password": TEMP_PASSWORD}, headers=admin).json()
    client.patch(f"/api/admin/staff/{staff['staff_id']}", json={"name": "Audited"}, headers=admin)
    client.post(f"/api/admin/staff/{staff['staff_id']}/reset-password",
                json={"temporary_password": TEMP_PASSWORD}, headers=admin)
    client.post(f"/api/admin/staff/{staff['staff_id']}/deactivate", headers=admin)
    dept = client.post("/api/admin/departments", json={"name": "Escalations", "categories": []},
                       headers=admin).json()
    client.put(f"/api/admin/departments/{dept['department_id']}",
               json={"name": "Escalations Team", "categories": []}, headers=admin)
    client.delete(f"/api/admin/departments/{dept['department_id']}", headers=admin)
    client.post("/api/admin/customers", json={"name": "C", "email": "c-audit@example.com",
                                              "temporary_password": TEMP_PASSWORD}, headers=admin)
    client.put("/api/admin/settings/incidents", json={"window_hours": 6, "min_tickets": 3,
                                                       "similarity": 0.8, "routing_confidence": 0.6},
               headers=admin)

    events = _audit(client, limit=200)
    actions = {e["action"] for e in events}
    for expected in ("staff.created", "staff.updated", "staff.password_reset", "staff.deactivated",
                     "department.created", "department.updated", "department.deleted",
                     "customer.created", "settings.updated"):
        assert expected in actions, expected
    updated = next(e for e in events if e["action"] == "staff.updated")
    assert updated["detail"]["fields"] == ["name"]
    settings = next(e for e in events if e["action"] == "settings.updated")
    assert settings["detail"]["fields"] == ["window_hours"]
    assert TEMP_PASSWORD not in json.dumps(events)


def test_audit_records_login_failures(client):
    _, email = _signup(client)
    _login(client, email, "wrong-password")
    _login(client, "nobody@example.com", "whatever-pass")
    failures = _audit(client, action="auth.login_failed")
    assert {f["detail"]["email"] for f in failures} >= {email, "nobody@example.com"}
    assert all("wrong-password" not in json.dumps(f) for f in failures)
    assert failures[0]["actor_name"] == "Anonymous"


def test_audit_records_incident_reviews(client, monkeypatch):
    from backend.ai_service import correlate_incidents
    from backend.ai_service.schemas import CorrelationJudgement

    monkeypatch.setattr(correlate_incidents, "correlate_incidents", lambda data: CorrelationJudgement(
        is_related=True, related_ticket_ids=[t.ticket_id for t in data.tickets], why_related="same",
        confidence=0.8))
    for _ in range(3):
        _raise(client, _signup(client)[0])
    (incident,) = client.get("/api/incidents", headers=_admin(client)).json()
    client.post(f"/api/incidents/{incident['incident_id']}/review", json={"action": "confirm", "note": "ok"},
                headers=_demo(client, "manager"))
    client.put(f"/api/incidents/{incident['incident_id']}/departments",
               json={"department_ids": [_departments(client)["Network"]]}, headers=_admin(client))
    actions = [e["action"] for e in _audit(client, entity="incident")]
    assert actions[:2] == ["incident.departments_changed", "incident.confirm"]


# ---------------------------------------------------------------------------
# Email off by default
# ---------------------------------------------------------------------------
def test_real_email_is_off_unless_enabled(client, monkeypatch):
    sent = []
    monkeypatch.setattr(email_utils, "send_email", lambda *a: sent.append(a))
    monkeypatch.setenv("SMTP_HOST", "smtp.test.invalid")
    monkeypatch.setenv("SMTP_FROM", "support@test.invalid")
    customer, _ = _signup(client)
    _raise(client, customer)  # human route -> email-style hand-off reply
    assert sent == []
    assert email_utils.email_enabled() is False

    monkeypatch.setenv("EMAIL_ENABLED", "true")
    _raise(client, customer)
    assert len(sent) == 1


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------
def test_attachments_only_through_the_authorised_endpoint(client, tmp_path):
    customer, _ = _signup(client)
    created = _raise(client, customer)
    res = client.post(f"/api/tickets/{created['ticket_id']}/attachments",
                      data={"message_id": created["message_id"]},
                      files=[("files", ("../../etc/evil.png", PNG, "image/png"))], headers=customer)
    assert res.status_code == 200, res.text
    (attachment,) = res.json()
    assert attachment["filename"] == "evil.png"  # path stripped from the display name
    stored = list((tmp_path / "uploads").iterdir())
    assert len(stored) == 1 and stored[0].parent == tmp_path / "uploads"

    url = f"/api/tickets/{created['ticket_id']}/attachments/{attachment['attachment_id']}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=_signup(client)[0]).status_code == 403
    for headers in (customer, _demo(client, "technician"), _admin(client)):
        assert client.get(url, headers=headers).content == PNG
    # Never served statically.
    assert client.get(f"/uploads/{stored[0].name}").status_code == 404
