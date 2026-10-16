"""Stage 1 roles: single env-bootstrapped admin, unified login, admin-only
staff creation, forced password change, departments, and backend role
enforcement on every portal's endpoints.

Real temporary SQLite DB via FastAPI's TestClient (startup runs
db.init_db() + auth.ensure_admin()); no LLM calls are made.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from backend import db, main, seed_data  # noqa: E402

ADMIN_EMAIL = "root-admin@example.com"
ADMIN_PASSWORD = "admin-test-password"


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


@pytest.fixture()
def client(db_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    with TestClient(main.app) as c:
        yield c


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _admin_headers(client) -> dict:
    res = _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert res.status_code == 200, res.text
    return _bearer(res.json()["token"])


def _demo(role: str) -> dict:
    (person,) = [p for p in seed_data.DEMO_STAFF if p["role"] == role]
    return person


def _headers_for(client, role: str) -> dict:
    if role == "admin":
        return _admin_headers(client)
    if role == "customer":
        res = client.post(
            "/api/auth/customer/signup",
            json={"name": "Cust", "email": "cust@example.com", "password": "cust-password"},
        )
        assert res.status_code == 200, res.text
        return _bearer(res.json()["token"])
    person = _demo(role)
    res = _login(client, person["email"], person["password"])
    assert res.status_code == 200, res.text
    return _bearer(res.json()["token"])


def _create_staff(client, role="manager", email="new-manager@example.com", password="temp-pass-123"):
    return client.post(
        "/api/admin/staff",
        json={"name": "New Person", "email": email, "role": role, "temporary_password": password},
        headers=_admin_headers(client),
    )


# ---------------------------------------------------------------------------
# Single admin bootstrap
# ---------------------------------------------------------------------------
def test_admin_seeded_once_from_env_and_never_overwritten(db_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    with TestClient(main.app) as c:
        res = _login(c, ADMIN_EMAIL, ADMIN_PASSWORD)
        assert res.status_code == 200
        assert res.json()["role"] == "admin"

    # Restart with a different password in env: the existing admin wins.
    monkeypatch.setenv("ADMIN_PASSWORD", "a-different-password")
    with TestClient(main.app) as c:
        assert _login(c, ADMIN_EMAIL, ADMIN_PASSWORD).status_code == 200
        assert _login(c, ADMIN_EMAIL, "a-different-password").status_code == 401

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 1


def test_production_without_admin_env_refuses_to_start(db_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="ADMIN_EMAIL"):
        with TestClient(main.app):
            pass


def test_dev_without_admin_env_uses_fallback_with_loud_warning(db_path, caplog):
    with caplog.at_level(logging.WARNING, logger="backend.auth"):
        with TestClient(main.app) as c:
            res = _login(c, "admin@example.com", "admin-dev-password")
            assert res.status_code == 200
            assert res.json()["role"] == "admin"
    assert "DEV-ONLY admin" in caplog.text


def test_demo_staff_not_seeded_in_production(db_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    with TestClient(main.app) as c:
        tech = _demo("technician")
        assert _login(c, tech["email"], tech["password"]).status_code == 401


# ---------------------------------------------------------------------------
# No public staff/admin signup
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path", ["/api/auth/admin/signup", "/api/auth/agent/signup",
             "/api/auth/technician/signup", "/api/auth/manager/signup"],
)
def test_no_public_signup_for_staff_or_admin(client, path):
    res = client.post(path, json={"name": "x", "email": "x@example.com", "password": "password123"})
    assert res.status_code in (404, 405)


def test_customer_signup_is_public_and_rejects_staff_emails(client):
    res = client.post(
        "/api/auth/customer/signup",
        json={"name": "C", "email": "c@example.com", "password": "password123"},
    )
    assert res.status_code == 200
    assert res.json()["role"] == "customer"

    taken = client.post(
        "/api/auth/customer/signup",
        json={"name": "C", "email": ADMIN_EMAIL, "password": "password123"},
    )
    assert taken.status_code == 400


# ---------------------------------------------------------------------------
# Admin creates staff; forced password change
# ---------------------------------------------------------------------------
def test_admin_creates_manager_who_must_change_password_first(client):
    res = _create_staff(client)
    assert res.status_code == 200, res.text
    created = res.json()
    assert created["role"] == "manager"
    assert created["must_change_password"] is True

    login = _login(client, "new-manager@example.com", "temp-pass-123")
    assert login.status_code == 200
    body = login.json()
    assert body["role"] == "manager"
    assert body["must_change_password"] is True
    headers = _bearer(body["token"])

    blocked = client.get("/api/manager/departments", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Password change required"

    changed = client.post(
        "/api/auth/change-password",
        json={"current_password": "temp-pass-123", "new_password": "my-own-pass-456"},
        headers=headers,
    )
    assert changed.status_code == 200, changed.text
    assert client.get("/api/auth/me", headers=headers).json()["must_change_password"] is False
    assert client.get("/api/manager/departments", headers=headers).status_code == 200
    assert _login(client, "new-manager@example.com", "my-own-pass-456").status_code == 200


@pytest.mark.parametrize("role", ["manager", "technician", "customer"])
def test_only_admin_can_create_users(client, role):
    headers = _headers_for(client, role)
    res = client.post(
        "/api/admin/staff",
        json={"name": "x", "email": "sneaky@example.com", "role": "manager",
              "temporary_password": "temp-pass-123"},
        headers=headers,
    )
    assert res.status_code == 403


def test_staff_role_must_be_technician_or_manager(client):
    res = _create_staff(client, role="admin")
    assert res.status_code == 422


def test_deactivated_staff_cannot_log_in_and_live_token_stops_working(client):
    headers = _headers_for(client, "technician")
    assert client.get("/api/staff/tickets", headers=headers).status_code == 200

    tech = _demo("technician")
    staff = client.get("/api/admin/staff", headers=_admin_headers(client)).json()
    (row,) = [s for s in staff if s["email"] == tech["email"]]
    res = client.post(f"/api/admin/staff/{row['staff_id']}/deactivate", headers=_admin_headers(client))
    assert res.status_code == 200

    assert client.get("/api/staff/tickets", headers=headers).status_code == 403
    assert _login(client, tech["email"], tech["password"]).status_code == 403

    client.post(f"/api/admin/staff/{row['staff_id']}/activate", headers=_admin_headers(client))
    assert _login(client, tech["email"], tech["password"]).status_code == 200


def test_reset_password_forces_change_on_next_login(client):
    staff_id = _create_staff(client, role="technician", email="t2@example.com").json()["staff_id"]
    res = client.post(
        f"/api/admin/staff/{staff_id}/reset-password",
        json={"temporary_password": "reset-pass-789"},
        headers=_admin_headers(client),
    )
    assert res.status_code == 200
    login = _login(client, "t2@example.com", "reset-pass-789")
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    assert _login(client, "t2@example.com", "temp-pass-123").status_code == 401


# ---------------------------------------------------------------------------
# Unified login routes each role to its own dashboard
# ---------------------------------------------------------------------------
_DASHBOARD_ENDPOINT = {
    "customer": "/api/tickets/mine",
    "technician": "/api/staff/tickets",
    "manager": "/api/manager/departments",
    "admin": "/api/admin/tickets",
}


@pytest.mark.parametrize("role", list(_DASHBOARD_ENDPOINT))
def test_each_role_lands_on_its_own_dashboard_only(client, role):
    headers = _headers_for(client, role)
    me = client.get("/api/auth/me", headers=headers)
    assert me.json()["role"] == role
    for other_role, endpoint in _DASHBOARD_ENDPOINT.items():
        res = client.get(endpoint, headers=headers)
        expected = 200 if other_role == role else 403
        assert res.status_code == expected, (role, endpoint, res.text)


def test_unified_login_rejects_bad_password(client):
    assert _login(client, ADMIN_EMAIL, "wrong-password").status_code == 401
    assert _login(client, "nobody@example.com", "whatever-pass").status_code == 401


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------
def test_default_departments_cover_every_category_once(client):
    res = client.get("/api/admin/departments", headers=_admin_headers(client)).json()
    owned = [c for d in res["departments"] for c in d["categories"]]
    assert sorted(owned) == sorted(res["categories"])
    assert len(owned) == len(set(owned))


def test_department_crud_and_validation(client):
    headers = _admin_headers(client)
    # ALERTS is already owned by a default department.
    clash = client.post("/api/admin/departments", json={"name": "Dupes", "categories": ["ALERTS"]},
                        headers=headers)
    assert clash.status_code == 400
    unknown = client.post("/api/admin/departments", json={"name": "Bad", "categories": ["NOPE"]},
                          headers=headers)
    assert unknown.status_code == 400

    created = client.post("/api/admin/departments", json={"name": "Escalations", "categories": []},
                          headers=headers)
    assert created.status_code == 200, created.text
    dept_id = created.json()["department_id"]

    # Move SUPPORT out of its default department into the new one.
    depts = client.get("/api/admin/departments", headers=headers).json()["departments"]
    (owner,) = [d for d in depts if "SUPPORT" in d["categories"]]
    client.put(f"/api/admin/departments/{owner['department_id']}",
               json={"name": owner["name"], "categories": [c for c in owner["categories"] if c != "SUPPORT"]},
               headers=headers)
    moved = client.put(f"/api/admin/departments/{dept_id}",
                       json={"name": "Escalations", "categories": ["SUPPORT"]}, headers=headers)
    assert moved.status_code == 200
    assert moved.json()["categories"] == ["SUPPORT"]

    assert client.delete(f"/api/admin/departments/{dept_id}", headers=headers).status_code == 200
    assert client.delete(f"/api/admin/departments/{dept_id}", headers=headers).status_code == 404


def test_admin_assigns_manager_departments_and_manager_sees_them(client):
    headers = _admin_headers(client)
    depts = client.get("/api/admin/departments", headers=headers).json()["departments"]
    network = next(d for d in depts if d["name"] == "Network")
    manager = _demo("manager")
    staff = client.get("/api/admin/staff", headers=headers).json()
    (row,) = [s for s in staff if s["email"] == manager["email"]]

    res = client.put(f"/api/admin/staff/{row['staff_id']}/departments",
                     json={"department_ids": [network["department_id"]]}, headers=headers)
    assert res.status_code == 200

    mine = client.get("/api/manager/departments", headers=_headers_for(client, "manager")).json()
    assert [d["name"] for d in mine] == ["Network"]
    assert set(mine[0]["categories"]) == {"SNMP", "NETFLOW"}


def test_technicians_cannot_own_departments(client):
    headers = _admin_headers(client)
    tech_id = _create_staff(client, role="technician", email="t3@example.com").json()["staff_id"]
    res = client.put(f"/api/admin/staff/{tech_id}/departments", json={"department_ids": []},
                     headers=headers)
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Legacy agents -> technicians migration
# ---------------------------------------------------------------------------
def test_legacy_agents_migrate_to_technicians_keeping_assignments(db_path, monkeypatch):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE customers (customer_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL);
        CREATE TABLE agents (agent_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL);
        CREATE TABLE tickets (ticket_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL REFERENCES customers(customer_id),
            channel TEXT NOT NULL, ticket_text TEXT NOT NULL, category TEXT,
            confidence REAL, route TEXT NOT NULL, ai_response TEXT, status TEXT NOT NULL,
            assigned_agent_id TEXT REFERENCES agents(agent_id),
            clarification_attempts INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP NOT NULL);
        INSERT INTO customers VALUES ('CUST-1', 'C', 'c@example.com', 'x');
        INSERT INTO agents VALUES ('AGENT-1', 'Old Agent', 'old-agent@example.com', 'x');
        INSERT INTO tickets VALUES ('TCK-1', 'CUST-1', 'chat', 'help', NULL, NULL, 'human',
            NULL, 'assigned', 'AGENT-1', 0, '2026-01-01T00:00:00+00:00');
    """)
    conn.commit()
    conn.close()

    db.init_db()

    conn = db.get_connection()
    staff = dict(conn.execute("SELECT * FROM staff WHERE staff_id = 'AGENT-1'").fetchone())
    assert staff["role"] == "technician"
    assert staff["active"] == 1
    ticket = conn.execute("SELECT assigned_technician_id FROM tickets WHERE ticket_id = 'TCK-1'").fetchone()
    assert ticket["assigned_technician_id"] == "AGENT-1"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agents'"
    ).fetchone()
