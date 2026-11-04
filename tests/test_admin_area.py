"""Admin area: user management, the ticket dashboard and SLA.

- Every admin endpoint is admin-only on the server: a customer, technician
  or manager gets 403, no token 401.
- Users: create customer/technician/manager (never admin) with a generated
  temporary password returned once (no-store), never in a list, never in
  the audit log, stored only hashed; first sign-in must change it; reset
  works the same way; a deactivated user can't sign in and keeps their
  tickets; a customer can't be edited into staff.
- Dashboard: counts by status/priority, per-day series, open by priority
  and technician, average first-response and resolution times.
- SLA: off by default; when on, due times from the priority at creation,
  recalculated on a priority change; first response, breach, at-risk, the
  resolution clock pausing in Pending Customer and resuming; the SLA
  dashboard's compliance and watch list; staff see the SLA on tickets.

Runs on SQLite and, with DB_BACKEND=postgres, on Postgres (tests/conftest.py).
The routing pipeline is a stub that sends every ticket to a human.
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import app_settings, db, main, runtime, seed_data, sla

_counter = itertools.count()
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


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


def _customer(client):
    email = f"cust-{next(_counter)}@example.com"
    res = client.post("/api/auth/customer/signup", json={"name": "Dana", "email": email, "password": "password123"})
    return _bearer(res.json()["token"]), email


def _raise(client, headers, priority="medium"):
    res = client.post("/api/tickets", json={"subject": "Monitor down", "description": "The website monitor is down.",
                                            "priority": priority}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["ticket_id"]


def _sql(sql, params=()):
    conn = db.get_connection()
    try:
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


def _enable_sla(client, **overrides):
    values = {**app_settings.SLASettings().model_dump(), "enabled": True, **overrides}
    res = client.put("/api/admin/settings/sla", json=values, headers=_admin(client))
    assert res.status_code == 200, res.text


def _iso(dt):
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------
ADMIN_ENDPOINTS = [
    ("get", "/api/admin/users", None),
    ("post", "/api/admin/users", {"name": "X", "email": "x-authz@example.com", "role": "customer"}),
    ("patch", "/api/admin/users/CUST-x", {"name": "Y"}),
    ("post", "/api/admin/users/CUST-x/deactivate", None),
    ("post", "/api/admin/users/CUST-x/activate", None),
    ("post", "/api/admin/users/CUST-x/reset-password", None),
    ("post", "/api/admin/staff", {"name": "X", "email": "x2-authz@example.com", "role": "technician"}),
    ("post", "/api/admin/customers/CUST-x/reset-password", None),
    ("get", "/api/admin/dashboard", None),
    ("get", "/api/admin/sla", None),
    ("post", "/api/admin/tickets/TCK-x/priority", {"priority": "high"}),
    ("put", "/api/admin/settings/sla", {"enabled": True}),
    ("get", "/api/admin/audit", None),
]


@pytest.mark.parametrize("role", ["customer", "technician", "manager"])
def test_admin_endpoints_are_forbidden_to_other_roles(client, role):
    headers = _customer(client)[0] if role == "customer" else _demo(client, role)
    for method, url, body in ADMIN_ENDPOINTS:
        res = getattr(client, method)(url, headers=headers, **({"json": body} if body else {}))
        assert res.status_code == 403, (role, method, url, res.status_code)


def test_admin_endpoints_need_a_token(client):
    for method, url, body in ADMIN_ENDPOINTS:
        res = getattr(client, method)(url, **({"json": body} if body else {}))
        assert res.status_code == 401, (method, url)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["customer", "technician", "manager"])
def test_created_user_gets_a_one_time_password_and_must_change_it(client, role):
    admin = _admin(client)
    email = f"new-{role}@example.com"
    res = client.post("/api/admin/users", json={"name": f"New {role}", "email": email, "role": role}, headers=admin)
    assert res.status_code == 200, res.text
    user = res.json()
    temp = user.pop("temporary_password")
    assert res.headers["cache-control"] == "no-store"
    assert user["role"] == role and user["must_change_password"] is True and user["active"] is True

    # Never shown again, never stored in clear.
    listed = client.get("/api/admin/users", params={"q": email}, headers=admin).json()
    assert [u["user_id"] for u in listed] == [user["user_id"]] and "temporary_password" not in listed[0]
    table, id_col = ("customers", "customer_id") if role == "customer" else ("staff", "staff_id")
    (stored,) = _sql(f"SELECT password_hash FROM {table} WHERE {id_col} = ?", (user["user_id"],))
    assert temp not in stored["password_hash"] and stored["password_hash"].startswith("$2")

    login = _login(client, email, temp).json()
    assert login["must_change_password"] is True
    headers = _bearer(login["token"])
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    blocked = client.get("/api/tickets/mine" if role == "customer" else "/api/staff/tickets", headers=headers)
    assert blocked.status_code == 403
    changed = client.post("/api/auth/change-password",
                          json={"current_password": temp, "new_password": "my-own-pass-456"}, headers=headers)
    assert changed.status_code == 200, changed.text
    assert _login(client, email, temp).status_code == 401


@pytest.mark.parametrize("role", ["admin", "superuser"])
def test_admin_accounts_cannot_be_created_here(client, role):
    res = client.post("/api/admin/users", json={"name": "X", "email": "x-admin@example.com", "role": role},
                      headers=_admin(client))
    assert res.status_code == 400


def test_the_admin_account_is_not_managed_here(client):
    admin = _admin(client)
    admin_id = client.get("/api/auth/me", headers=admin).json()["user_id"]
    for action in ("deactivate", "reset-password"):
        assert client.post(f"/api/admin/users/{admin_id}/{action}", headers=admin).status_code == 403
    assert client.patch(f"/api/admin/users/{admin_id}", json={"name": "X"}, headers=admin).status_code == 403
    assert admin_id not in [u["user_id"] for u in client.get("/api/admin/users", headers=admin).json()]


def test_reset_password_replaces_the_old_one(client):
    admin = _admin(client)
    _, email = _customer(client)
    (user,) = client.get("/api/admin/users", params={"q": email}, headers=admin).json()
    res = client.post(f"/api/admin/users/{user['user_id']}/reset-password", headers=admin)
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    temp = res.json()["temporary_password"]
    assert _login(client, email, "password123").status_code == 401
    assert _login(client, email, temp).json()["must_change_password"] is True


def test_deactivated_user_cannot_sign_in_and_keeps_their_tickets(client):
    admin = _admin(client)
    headers, email = _customer(client)
    ticket_id = _raise(client, headers)
    (user,) = client.get("/api/admin/users", params={"q": email}, headers=admin).json()

    off = client.post(f"/api/admin/users/{user['user_id']}/deactivate", headers=admin).json()
    assert off["active"] is False
    assert _login(client, email, "password123").status_code == 403
    assert client.get("/api/tickets/mine", headers=headers).status_code == 403  # live session too
    assert _sql("SELECT ticket_id FROM tickets WHERE ticket_id = ?", (ticket_id,))
    assert [u["user_id"] for u in client.get("/api/admin/users", params={"status": "inactive"},
                                             headers=admin).json()] == [user["user_id"]]

    client.post(f"/api/admin/users/{user['user_id']}/activate", headers=admin)
    assert _login(client, email, "password123").status_code == 200


def test_edit_name_email_and_staff_role(client):
    admin = _admin(client)
    tech = client.post("/api/admin/users", json={"name": "Tia", "email": "tia@example.com", "role": "technician"},
                       headers=admin).json()
    res = client.patch(f"/api/admin/users/{tech['user_id']}", json={"name": "Tia Tech", "role": "manager"},
                       headers=admin)
    assert res.status_code == 200, res.text
    assert (res.json()["name"], res.json()["role"]) == ("Tia Tech", "manager")
    assert client.patch(f"/api/admin/users/{tech['user_id']}", json={"role": "customer"},
                        headers=admin).status_code == 400
    _, email = _customer(client)
    (customer,) = client.get("/api/admin/users", params={"q": email}, headers=admin).json()
    assert client.patch(f"/api/admin/users/{customer['user_id']}", json={"role": "technician"},
                        headers=admin).status_code == 400
    assert client.patch(f"/api/admin/users/{customer['user_id']}", json={"email": "tia@example.com"},
                        headers=admin).status_code == 400  # taken


def test_user_actions_are_audited_without_passwords(client):
    admin = _admin(client)
    created = client.post("/api/admin/users", json={"name": "Aud", "email": "aud@example.com", "role": "customer"},
                          headers=admin).json()
    reset = client.post(f"/api/admin/users/{created['user_id']}/reset-password", headers=admin).json()
    client.post(f"/api/admin/users/{created['user_id']}/deactivate", headers=admin)
    events = client.get("/api/admin/audit", params={"entity_id": created["user_id"]}, headers=admin).json()["events"]
    assert [e["action"] for e in events] == ["customer.deactivated", "customer.password_reset", "customer.created"]
    assert all(e["actor_name"] and e["at"] for e in events)
    text = json.dumps(events)
    assert created["temporary_password"] not in text and reset["temporary_password"] not in text


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def test_dashboard_counts_series_and_response_times(client):
    admin, tech = _admin(client), _demo(client, "technician")
    headers, _ = _customer(client)
    urgent, low, other = _raise(client, headers, "urgent"), _raise(client, headers, "low"), _raise(client, headers)
    created = NOW - timedelta(hours=3)
    _sql("UPDATE tickets SET created_at = ?", (_iso(created),))
    client.post(f"/api/staff/tickets/{urgent}/claim", headers=tech)
    client.post(f"/api/staff/tickets/{urgent}/messages", headers=tech,
                json={"body": "Looking now.", "visibility": "public_reply"})
    client.post(f"/api/staff/tickets/{low}/claim", headers=tech)
    client.post(f"/api/staff/tickets/{low}/status", headers=tech, json={"status": "resolved"})
    _sql("UPDATE tickets SET first_response_at = ? WHERE ticket_id = ?", (_iso(created + timedelta(minutes=30)), urgent))
    _sql("UPDATE tickets SET resolved_at = ? WHERE ticket_id = ?", (_iso(created + timedelta(hours=2)), low))

    day = created.date().isoformat()
    res = client.get("/api/admin/dashboard", params={"since": day, "until": day}, headers=admin)
    assert res.status_code == 200, res.text
    d = res.json()
    assert d["total"] == 3
    assert d["by_status"]["in_progress"] == 1 and d["by_status"]["resolved"] == 1 and d["by_status"]["open"] == 1
    assert d["by_priority"] == {"low": 1, "medium": 1, "high": 0, "urgent": 1}
    assert d["over_time"][0]["date"] == day and d["over_time"][0]["created"] == 3
    assert d["open_by_priority"] == {"low": 0, "medium": 1, "high": 0, "urgent": 1}
    by_tech = {e["name"]: e["count"] for e in d["open_by_technician"]}
    assert by_tech == {"Demo Technician": 1, "Unassigned": 1}
    assert d["response_times"]["urgent"]["avg_first_response_minutes"] == 30.0
    assert d["response_times"]["low"]["avg_resolution_minutes"] == 120.0

    only_low = client.get("/api/admin/dashboard", params={"since": day, "until": day, "priority": "low"},
                          headers=admin).json()
    assert only_low["total"] == 1 and other not in json.dumps(only_low)
    assert client.get("/api/admin/dashboard", params={"since": "2026-12-01", "until": "2026-11-01"},
                      headers=admin).status_code == 400


# ---------------------------------------------------------------------------
# SLA: settings and due times
# ---------------------------------------------------------------------------
def test_sla_is_off_until_the_admin_turns_it_on(client):
    headers, _ = _customer(client)
    before = _raise(client, headers, "urgent")
    (row,) = _sql("SELECT sla_resolution_due FROM tickets WHERE ticket_id = ?", (before,))
    assert row["sla_resolution_due"] is None
    assert client.get("/api/admin/sla", headers=_admin(client)).json()["enabled"] is False

    _enable_sla(client)
    after = _raise(client, headers, "urgent")
    (row,) = _sql(f"SELECT {sla.SLA_COLUMNS} FROM tickets WHERE ticket_id = ?", (after,))
    created = sla.parse(row["created_at"])
    assert sla.parse(row["sla_first_response_due"]) == created + timedelta(minutes=30)
    assert sla.parse(row["sla_resolution_due"]) == created + timedelta(hours=4)
    (old,) = _sql("SELECT sla_resolution_due FROM tickets WHERE ticket_id = ?", (before,))
    assert old["sla_resolution_due"] is None  # not applied retroactively


def test_invalid_sla_targets_are_rejected(client):
    bad = {**app_settings.SLASettings().model_dump(), "enabled": True,
           "high": {"first_response_minutes": 120, "resolution_minutes": 60}}
    assert client.put("/api/admin/settings/sla", json=bad, headers=_admin(client)).status_code == 422
    bad = {**app_settings.SLASettings().model_dump(), "calendar": "business_hours"}
    assert client.put("/api/admin/settings/sla", json=bad, headers=_admin(client)).status_code == 422


def test_priority_change_recalculates_the_due_times(client):
    _enable_sla(client)
    headers, _ = _customer(client)
    ticket_id = _raise(client, headers, "low")
    res = client.post(f"/api/admin/tickets/{ticket_id}/priority", json={"priority": "urgent"}, headers=_admin(client))
    assert res.status_code == 200, res.text
    (row,) = _sql(f"SELECT {sla.SLA_COLUMNS} FROM tickets WHERE ticket_id = ?", (ticket_id,))
    created = sla.parse(row["created_at"])
    assert row["priority"] == "urgent" and row["sla_resolution_minutes"] == 240
    assert sla.parse(row["sla_resolution_due"]) == created + timedelta(hours=4)  # from creation, not now
    actions = [e["action"] for e in client.get("/api/admin/audit", params={"entity_id": ticket_id},
                                               headers=_admin(client)).json()["events"]]
    assert "ticket.priority_changed" in actions


def test_first_response_is_recorded_by_the_first_staff_reply(client):
    _enable_sla(client)
    headers, _ = _customer(client)
    tech = _demo(client, "technician")
    ticket_id = _raise(client, headers, "high")
    client.post(f"/api/staff/tickets/{ticket_id}/claim", headers=tech)
    client.post(f"/api/staff/tickets/{ticket_id}/messages", headers=tech,
                json={"body": "Private note", "visibility": "internal_note"})
    assert _sql("SELECT first_response_at FROM tickets WHERE ticket_id = ?", (ticket_id,))[0]["first_response_at"] is None
    client.post(f"/api/staff/tickets/{ticket_id}/messages", headers=tech,
                json={"body": "On it.", "visibility": "public_reply"})
    (row,) = _sql(f"SELECT {sla.SLA_COLUMNS} FROM tickets WHERE ticket_id = ?", (ticket_id,))
    assert row["first_response_at"] is not None
    assert sla.evaluate(row)["first_response"]["state"] == "met"


def test_resolution_clock_pauses_while_pending_customer_and_resumes(client):
    _enable_sla(client)
    headers, _ = _customer(client)
    tech = _demo(client, "technician")
    ticket_id = _raise(client, headers, "high")  # 8 h to resolve
    client.post(f"/api/staff/tickets/{ticket_id}/claim", headers=tech)
    client.post(f"/api/staff/tickets/{ticket_id}/status", headers=tech, json={"status": "pending_customer"})
    (row,) = _sql("SELECT sla_paused_at FROM tickets WHERE ticket_id = ?", (ticket_id,))
    assert row["sla_paused_at"] is not None

    # Pretend: created 9 h ago, waiting on the customer for the last 6 h.
    created = sla.now() - timedelta(hours=9)
    _sql("UPDATE tickets SET created_at = ?, sla_paused_at = ?, sla_resolution_due = ?, first_response_at = ? "
         "WHERE ticket_id = ?", (_iso(created), _iso(sla.now() - timedelta(hours=6)),
                                 _iso(created + timedelta(hours=8)), _iso(created + timedelta(minutes=10)), ticket_id))
    (row,) = _sql(f"SELECT {sla.SLA_COLUMNS} FROM tickets WHERE ticket_id = ?", (ticket_id,))
    result = sla.evaluate(row)["resolution"]
    assert result["state"] == "paused" and result["used_ratio"] == pytest.approx(3 / 8, abs=0.01)

    client.post(f"/api/tickets/{ticket_id}/reply", json={"message": "Here is the log."}, headers=headers)
    (row,) = _sql(f"SELECT {sla.SLA_COLUMNS} FROM tickets WHERE ticket_id = ?", (ticket_id,))
    assert row["status"] == "open" and row["sla_paused_at"] is None
    assert row["sla_paused_seconds"] == pytest.approx(6 * 3600, abs=5)
    result = sla.evaluate(row)
    assert result["resolution"]["state"] == "on_track" and result["within_sla"] is True
    assert sla.parse(result["resolution"]["due_at"]) == pytest.approx(
        created + timedelta(hours=14), abs=timedelta(seconds=5))  # 8 h target + 6 h paused


def test_sla_dashboard_compliance_and_watchlist(client):
    _enable_sla(client)
    admin, tech = _admin(client), _demo(client, "technician")
    headers, _ = _customer(client)
    late, risky, fine = _raise(client, headers, "urgent"), _raise(client, headers, "urgent"), _raise(client, headers, "urgent")
    now = sla.now()

    def backdate(ticket_id, minutes):
        created = now - timedelta(minutes=minutes)
        _sql("UPDATE tickets SET created_at = ?, sla_first_response_due = ?, sla_resolution_due = ? "
             "WHERE ticket_id = ?", (_iso(created), _iso(created + timedelta(minutes=30)),
                                     _iso(created + timedelta(hours=4)), ticket_id))

    backdate(late, 300)   # 5 h old, no reply: both targets breached
    backdate(risky, 200)  # 3 h 20 old: resolution 83% used, first response breached
    backdate(fine, 10)
    _sql("UPDATE tickets SET first_response_at = ? WHERE ticket_id = ?", (_iso(now - timedelta(minutes=190)), risky))
    client.post(f"/api/staff/tickets/{fine}/claim", headers=tech)
    client.post(f"/api/staff/tickets/{fine}/messages", headers=tech, json={"body": "Hi", "visibility": "public_reply"})

    d = client.get("/api/admin/sla", headers=admin).json()
    assert d["enabled"] is True and d["tickets_with_sla"] == 3
    urgent = d["by_priority"]["urgent"]
    assert urgent["first_response"] == {"met": 2, "breached": 1, "percent": 66.7}
    assert urgent["breached"] == 1 and urgent["at_risk"] == 1 and urgent["within_sla"] == 2
    assert [w["ticket_id"] for w in d["watchlist"]] == [late, risky]
    assert d["watchlist"][1]["sla"]["resolution"]["state"] == "at_risk"


def test_staff_see_the_sla_on_tickets(client):
    _enable_sla(client)
    headers, _ = _customer(client)
    ticket_id = _raise(client, headers, "high")
    tech = _demo(client, "technician")
    (row,) = [t for t in client.get("/api/staff/tickets", params={"view": "unassigned"}, headers=tech).json()
              if t["ticket_id"] == ticket_id]
    assert row["sla"]["state"] == "on_track" and row["sla"]["resolution"]["target_minutes"] == 480
    assert not any(k.startswith("sla_") for k in row)
    detail = client.get(f"/api/staff/tickets/{ticket_id}", headers=tech).json()
    assert detail["ticket"]["sla"]["first_response"]["state"] == "on_track"
    # Customers never see SLA data.
    assert "sla" not in client.get(f"/api/tickets/{ticket_id}", headers=headers).text


# ---------------------------------------------------------------------------
# SLA math (pure)
# ---------------------------------------------------------------------------
def _row(**kw):
    created = NOW - timedelta(minutes=kw.pop("age", 0))
    row = {"created_at": _iso(created), "status": "open", "priority": "high", "first_response_at": None,
           "resolved_at": None, "sla_first_response_minutes": 60, "sla_resolution_minutes": 480,
           "sla_first_response_due": _iso(created + timedelta(minutes=60)),
           "sla_resolution_due": _iso(created + timedelta(minutes=480)), "sla_paused_at": None,
           "sla_paused_seconds": 0}
    row.update(kw)
    return row


S = app_settings.SLASettings(enabled=True)


@pytest.mark.parametrize("age, state", [(10, "on_track"), (44, "on_track"), (45, "at_risk"), (61, "breached")])
def test_first_response_states(age, state):
    assert sla.evaluate(_row(age=age), NOW, S)["first_response"]["state"] == state


def test_resolution_met_breached_and_late_reply():
    created = NOW - timedelta(hours=10)
    done = _row(age=600, status="resolved", resolved_at=_iso(created + timedelta(hours=7)),
                first_response_at=_iso(created + timedelta(minutes=90)))
    result = sla.evaluate(done, NOW, S)
    assert result["resolution"]["state"] == "met" and result["first_response"]["state"] == "breached"
    assert result["within_sla"] is False and result["state"] == "breached"
    late = _row(age=600, status="closed", resolved_at=_iso(created + timedelta(hours=9)))
    assert sla.evaluate(late, NOW, S)["resolution"]["state"] == "breached"


def test_no_sla_without_targets():
    assert sla.evaluate(_row(sla_resolution_minutes=None), NOW, S) is None


def test_at_risk_threshold_is_configurable():
    strict = app_settings.SLASettings(enabled=True, at_risk_percent=50)
    assert sla.evaluate(_row(age=31), NOW, strict)["first_response"]["state"] == "at_risk"


def test_status_changes_track_pauses_and_resolved_at(client):
    headers, _ = _customer(client)
    ticket_id = _raise(client, headers)
    conn = db.get_connection()
    try:
        t0 = NOW
        sla.on_status_changed(conn, ticket_id, "open", "pending_customer", _iso(t0))
        sla.on_status_changed(conn, ticket_id, "pending_customer", "in_progress", _iso(t0 + timedelta(minutes=20)))
        sla.on_status_changed(conn, ticket_id, "in_progress", "resolved", _iso(t0 + timedelta(minutes=30)))
        sla.on_status_changed(conn, ticket_id, "resolved", "open", _iso(t0 + timedelta(minutes=50)))
        conn.commit()
        row = dict(conn.execute("SELECT sla_paused_at, sla_paused_seconds, resolved_at FROM tickets "
                                "WHERE ticket_id = ?", (ticket_id,)).fetchone())
    finally:
        conn.close()
    # 20 min waiting on the customer + 20 min resolved, then reopened.
    assert row == {"sla_paused_at": None, "sla_paused_seconds": 2400, "resolved_at": None}


def test_calendar_is_24x7_only_for_now():
    assert sla.add_working_time(NOW, 90) == NOW + timedelta(minutes=90)
    assert sla.working_seconds(NOW, NOW + timedelta(hours=2)) == 7200
    with pytest.raises(ValueError):
        sla.add_working_time(NOW, 90, "business_hours")
