"""Sub-stage D: AI incident correlation with manager review
(backend/incidents.py).

Real temporary SQLite DB via FastAPI's TestClient. Every new ticket
queues a correlation pass as a background task, which TestClient runs
right after the response. The model is mocked: correlate_incidents is
replaced per test, and summarize_incident falls back (the provider is
disabled in tests/conftest.py). The routing pipeline is a stub that sends
each ticket to a human with the category the test chooses.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import db, main, runtime, seed_data
from backend.ai_service import correlate_incidents
from backend.ai_service.schemas import CorrelationJudgement

_counter = itertools.count()


class _HumanWithCategory:
    def __init__(self):
        self.category = "SERVER_MONITORING"

    def invoke(self, state):
        return {"route": "human", "category": self.category, "confidence": 0.5,
                "clarification_attempts": 0}


@pytest.fixture()
def pipeline():
    return _HumanWithCategory()


@pytest.fixture()
def client(tmp_path, monkeypatch, pipeline):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with TestClient(main.app) as c:
        runtime.set_graph(pipeline)
        yield c


@pytest.fixture()
def judge(monkeypatch):
    """Mock the model's judgement. Defaults: every ticket in the group is
    related, confidence 0.8, no extra departments."""
    state = {"calls": [], "related": None, "confidence": 0.8, "departments": [], "is_related": True}

    def _fake(data):
        state["calls"].append(data)
        ids = [t.ticket_id for t in data.tickets] if state["related"] is None else state["related"]
        return CorrelationJudgement(
            is_related=state["is_related"], related_ticket_ids=ids, why_related="Same symptom, same hour",
            title="Server monitors not collecting data", confidence=state["confidence"],
            candidate_departments=state["departments"], observed_facts=["3 reports of no data"],
            hypotheses=["Possible collector issue"],
        )

    monkeypatch.setattr(correlate_incidents, "correlate_incidents", _fake)
    return state


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _customer(client):
    res = client.post("/api/auth/customer/signup", json={
        "name": f"Customer {next(_counter)}", "email": f"inc-{next(_counter)}@example.com",
        "password": "password123"})
    return _bearer(res.json()["token"])


def _login(client, email, password):
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return _bearer(res.json()["token"])


def _admin(client):
    return _login(client, "admin@example.com", "admin-dev-password")


def _demo_manager(client):
    """Manages Infrastructure (AGENT, SERVER_MONITORING, ...)."""
    (p,) = [p for p in seed_data.DEMO_STAFF if p["role"] == "manager"]
    return _login(client, p["email"], p["password"])


def _network_manager(client):
    admin = _admin(client)
    email = f"net-mgr-{next(_counter)}@example.com"
    staff_id = client.post("/api/admin/staff", json={
        "name": "Network Manager", "email": email, "role": "manager",
        "temporary_password": "temp-pass-123"}, headers=admin).json()["staff_id"]
    depts = client.get("/api/admin/departments", headers=admin).json()["departments"]
    network = next(d for d in depts if d["name"] == "Network")
    client.patch(f"/api/admin/staff/{staff_id}",
                 json={"department_id": network["department_id"]}, headers=admin)
    headers = _login(client, email, "temp-pass-123")
    client.post("/api/auth/change-password",
                json={"current_password": "temp-pass-123", "new_password": "real-pass-456"}, headers=headers)
    return headers


def _raise(client, customer, pipeline, category="SERVER_MONITORING", subject="Server monitor not collecting data"):
    pipeline.category = category
    res = client.post("/api/tickets", json={
        "subject": subject, "description": "No data from our Linux servers since 10:00 UTC."}, headers=customer)
    assert res.status_code == 200, res.text
    return res.json()["ticket_id"]


def _query(sql, params=()):
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _clusters():
    return _query("SELECT * FROM incident_clusters")


def _cluster_tickets(incident_id):
    return {r["ticket_id"] for r in _query(
        "SELECT ticket_id FROM incident_tickets WHERE incident_id = ?", (incident_id,))}


def _backdate(ticket_id, hours):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = db.get_connection()
    conn.execute("UPDATE tickets SET created_at = ? WHERE ticket_id = ?", (stamp, ticket_id))
    conn.commit()
    conn.close()


def _three_customers_report(client, pipeline, **kwargs):
    return [_raise(client, _customer(client), pipeline, **kwargs) for _ in range(3)]


# ---------------------------------------------------------------------------
# Candidate generation + judgement
# ---------------------------------------------------------------------------
def test_similar_tickets_from_different_customers_form_one_cluster(client, pipeline, judge):
    ids = _three_customers_report(client, pipeline)
    (cluster,) = _clusters()
    assert cluster["status"] == "detected"
    assert _cluster_tickets(cluster["incident_id"]) == set(ids)
    # The pre-filter (3 tickets, 2+ customers) meant one model call, on the 3rd.
    assert len(judge["calls"]) == 1
    # The model only ever sees pseudonymous customers.
    assert [t.customer_ref for t in judge["calls"][0].tickets] == ["C1", "C2", "C3"]


def test_fewer_than_min_tickets_never_reaches_the_model(client, pipeline, judge):
    _raise(client, _customer(client), pipeline)
    _raise(client, _customer(client), pipeline)
    assert judge["calls"] == [] and _clusters() == []


def test_categories_of_one_department_are_grouped_together(client, pipeline, judge):
    """The classifier often splits one outage across AGENT and
    SERVER_MONITORING; both belong to Infrastructure, so they're one
    candidate group."""
    _raise(client, _customer(client), pipeline, category="AGENT")
    _raise(client, _customer(client), pipeline, category="SERVER_MONITORING")
    _raise(client, _customer(client), pipeline, category="AGENT")
    (cluster,) = _clusters()
    assert len(_cluster_tickets(cluster["incident_id"])) == 3
    assert cluster["category"] == "AGENT"


def test_different_departments_are_not_grouped(client, pipeline, judge):
    _raise(client, _customer(client), pipeline, category="AGENT")       # Infrastructure
    _raise(client, _customer(client), pipeline, category="NETFLOW")     # Network
    _raise(client, _customer(client), pipeline, category="ALERTS")      # Monitoring & Alerts
    assert judge["calls"] == [] and _clusters() == []


def test_one_customer_reporting_three_times_is_not_an_incident(client, pipeline, judge):
    customer = _customer(client)
    for _ in range(3):
        _raise(client, customer, pipeline)
    assert judge["calls"] == [] and _clusters() == []


def test_same_wording_outside_the_time_window_is_not_correlated(client, pipeline, judge):
    old = [_raise(client, _customer(client), pipeline) for _ in range(2)]
    for ticket_id in old:
        _backdate(ticket_id, 25)
    _raise(client, _customer(client), pipeline)
    assert judge["calls"] == [] and _clusters() == []


def test_window_is_configurable(client, pipeline, judge, monkeypatch):
    monkeypatch.setenv("INCIDENT_WINDOW_HOURS", "48")
    old = [_raise(client, _customer(client), pipeline) for _ in range(2)]
    for ticket_id in old:
        _backdate(ticket_id, 25)
    _raise(client, _customer(client), pipeline)
    assert len(_clusters()) == 1


def test_model_judging_them_unrelated_creates_nothing(client, pipeline, judge):
    judge["is_related"] = False
    _three_customers_report(client, pipeline)
    assert len(judge["calls"]) == 1 and _clusters() == []


def test_ai_failure_creates_nothing_and_tickets_are_untouched(client, pipeline):
    ids = _three_customers_report(client, pipeline)  # provider disabled: AIError
    assert _clusters() == []
    assert {r["status"] for r in _query("SELECT status FROM tickets")} == {"open"}
    assert len(ids) == 3


def test_ongoing_event_reuses_the_open_cluster(client, pipeline, judge):
    ids = _three_customers_report(client, pipeline)
    calls_before = len(judge["calls"])
    # Nothing new: an explicit re-run doesn't call the model again.
    client.post("/api/incidents/run", headers=_admin(client))
    assert len(judge["calls"]) == calls_before

    fourth = _raise(client, _customer(client), pipeline)
    (cluster,) = _clusters()
    assert _cluster_tickets(cluster["incident_id"]) == set(ids) | {fourth}


def test_correlation_never_closes_or_merges_tickets(client, pipeline, judge):
    ids = _three_customers_report(client, pipeline)
    (cluster,) = _clusters()
    client.post(f"/api/incidents/{cluster['incident_id']}/review",
                json={"action": "confirm", "note": "Collector outage confirmed"}, headers=_admin(client))
    rows = _query(f"SELECT ticket_id, status FROM tickets WHERE ticket_id IN ({','.join('?' * 3)})", tuple(ids))
    assert sorted(r["ticket_id"] for r in rows) == sorted(ids)
    assert {r["status"] for r in rows} == {"open"}


# ---------------------------------------------------------------------------
# Review workflow
# ---------------------------------------------------------------------------
def test_confirm_investigate_and_dismiss_are_logged(client, pipeline, judge):
    _three_customers_report(client, pipeline)
    (cluster,) = _clusters()
    url = f"/api/incidents/{cluster['incident_id']}/review"
    manager = _demo_manager(client)

    assert client.post(url, json={"action": "investigate", "note": "Looking"}, headers=manager).json()[
        "status"] == "under_investigation"
    assert client.post(url, json={"action": "confirm", "note": "Real"}, headers=manager).json()[
        "status"] == "confirmed"
    detail = client.post(url, json={"action": "dismiss", "note": "False alarm"}, headers=manager).json()
    assert detail["status"] == "dismissed"
    assert [(r["action"], r["note"]) for r in detail["reviews"]] == [
        ("investigate", "Looking"), ("confirm", "Real"), ("dismiss", "False alarm")]
    assert detail["reviews"][0]["reviewer_name"] == "Demo Manager"

    assert client.post(url, json={"action": "confirm", "note": ""}, headers=manager).status_code == 422


def test_dismissed_tickets_are_not_reclustered(client, pipeline, judge):
    _three_customers_report(client, pipeline)
    (cluster,) = _clusters()
    client.post(f"/api/incidents/{cluster['incident_id']}/review",
                json={"action": "dismiss", "note": "Unrelated"}, headers=_admin(client))
    calls = len(judge["calls"])
    client.post("/api/incidents/run", headers=_admin(client))
    assert len(judge["calls"]) == calls and len(_clusters()) == 1


def test_customer_banner_only_on_confirmed_clusters(client, pipeline, judge):
    customer = _customer(client)
    mine = _raise(client, customer, pipeline)
    _raise(client, _customer(client), pipeline)
    _raise(client, _customer(client), pipeline)
    (cluster,) = _clusters()
    assert client.get(f"/api/tickets/{mine}", headers=customer).json()["incident_notice"] is None

    client.post(f"/api/incidents/{cluster['incident_id']}/review",
                json={"action": "confirm", "note": "ok"}, headers=_admin(client))
    notice = client.get(f"/api/tickets/{mine}", headers=customer).json()["incident_notice"]
    assert notice == "We're aware of this issue affecting multiple customers and are working on it."


# ---------------------------------------------------------------------------
# Department routing + manager scope
# ---------------------------------------------------------------------------
def test_cluster_routes_to_the_owning_departments_manager_only(client, pipeline, judge):
    _three_customers_report(client, pipeline)  # SERVER_MONITORING -> Infrastructure
    (cluster,) = _clusters()
    infra, network = _demo_manager(client), _network_manager(client)

    assert [c["incident_id"] for c in client.get("/api/incidents", headers=infra).json()] == [cluster["incident_id"]]
    assert client.get("/api/incidents", headers=network).json() == []
    assert client.get(f"/api/incidents/{cluster['incident_id']}", headers=network).status_code == 403
    detail = client.get(f"/api/incidents/{cluster['incident_id']}", headers=infra).json()
    assert [d["name"] for d in detail["departments"]] == ["Infrastructure"]
    assert detail["departments"][0]["source"] == "ai"


def test_multi_department_cluster_surfaces_to_both_managers(client, pipeline, judge):
    judge["departments"] = ["Network"]
    _three_customers_report(client, pipeline)
    (cluster,) = _clusters()
    for manager in (_demo_manager(client), _network_manager(client)):
        assert [c["incident_id"] for c in client.get("/api/incidents", headers=manager).json()] == [
            cluster["incident_id"]]


def test_uncertain_cluster_is_left_for_routing(client, pipeline, judge):
    judge["confidence"] = 0.4  # below INCIDENT_ROUTING_CONFIDENCE (0.6)
    judge["departments"] = ["Network"]
    _three_customers_report(client, pipeline)
    (cluster,) = _clusters()
    assert cluster["needs_routing"] == 1
    assert _query("SELECT * FROM incident_departments") == []

    admin = _admin(client)
    depts = client.get("/api/admin/departments", headers=admin).json()["departments"]
    network = next(d for d in depts if d["name"] == "Network")
    detail = client.put(f"/api/incidents/{cluster['incident_id']}/departments",
                        json={"department_ids": [network["department_id"]]}, headers=admin).json()
    assert detail["needs_routing"] is False
    (department,) = detail["departments"]
    assert (department["name"], department["source"], department["confirmed"]) == ("Network", "manager", 1)
    assert detail["reviews"][-1]["action"] == "departments_changed"


def test_manager_corrections_survive_ai_reruns(client, pipeline, judge):
    _three_customers_report(client, pipeline)
    (cluster,) = _clusters()
    network = _network_manager(client)
    infra = _demo_manager(client)
    depts = client.get("/api/admin/departments", headers=_admin(client)).json()["departments"]
    by_name = {d["name"]: d["department_id"] for d in depts}
    client.put(f"/api/incidents/{cluster['incident_id']}/departments",
               json={"department_ids": [by_name["Infrastructure"], by_name["Network"]]}, headers=infra)

    _raise(client, _customer(client), pipeline)  # AI re-judges the grown group
    names = {d["name"] for d in client.get(f"/api/incidents/{cluster['incident_id']}", headers=network).json()[
        "departments"]}
    assert names == {"Infrastructure", "Network"}


def test_manager_must_keep_one_of_their_departments(client, pipeline, judge):
    _three_customers_report(client, pipeline)
    (cluster,) = _clusters()
    depts = client.get("/api/admin/departments", headers=_admin(client)).json()["departments"]
    network_id = next(d["department_id"] for d in depts if d["name"] == "Network")
    res = client.put(f"/api/incidents/{cluster['incident_id']}/departments",
                     json={"department_ids": [network_id]}, headers=_demo_manager(client))
    assert res.status_code == 400


def test_manager_can_open_linked_tickets_outside_their_categories(client, pipeline, judge):
    judge["departments"] = ["Infrastructure"]
    ids = _three_customers_report(client, pipeline, category="NETFLOW")  # owned by Network
    infra = _demo_manager(client)
    res = client.get(f"/api/staff/tickets/{ids[0]}", headers=infra)
    assert res.status_code == 200, res.text
    assert res.json()["incidents"][0]["status"] == "detected"


def test_only_managers_and_admin_use_incident_endpoints(client, pipeline, judge):
    _three_customers_report(client, pipeline)
    (cluster,) = _clusters()
    (tech,) = [p for p in seed_data.DEMO_STAFF if p["role"] == "technician"]
    tech_headers = _login(client, tech["email"], tech["password"])
    assert client.get("/api/incidents", headers=_customer(client)).status_code == 403
    # Technicians only see incidents assigned to them — none here.
    assert client.get("/api/incidents", headers=tech_headers).json() == []
    assert client.get(f"/api/incidents/{cluster['incident_id']}", headers=tech_headers).status_code == 403
    for headers in (_customer(client), tech_headers):
        assert client.post(f"/api/incidents/{cluster['incident_id']}/review",
                           json={"action": "confirm", "note": "x"}, headers=headers).status_code == 403
        assert client.post("/api/incidents/run", headers=headers).status_code == 403
