"""Limits on the paid AI endpoints and on sign-up (backend/rate_limit.py
check_ai_turn / check_signup, auth.get_ai_customer), the request size
caps, the 72-byte password limit, and the transcript cap sent to the
model (conversation.latest_transcript).

Starting a chat, a chat message, a new ticket and a ticket reply each
count as one AI turn: 20 per 10 minutes and 200 per day per customer,
higher per IP; over a limit -> 429 with Retry-After.

Real FastAPI app and temporary SQLite; the graph is a stub that always
answers, and the rate-limit clock is a fake.
"""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

from backend import auth, conversation, db, graph, main, rate_limit, runtime

_counter = itertools.count()


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _AnsweringGraph:
    def __init__(self):
        self.calls = 0

    def invoke(self, state):
        self.calls += 1
        return {"route": "ai", "category": "MONITORING", "confidence": 0.9, "clarification_attempts": 0,
                "ai_response": f"Here is answer {self.calls}.", "needs_info": None, "language_tags": []}


@pytest.fixture()
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(rate_limit, "_clock", fake)
    return fake


@pytest.fixture()
def client(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setenv("AI_RATE_LIMIT_ENABLED", "true")
    # A follow-up after an answer asks the model whether it continues the
    # thread; no model in tests.
    monkeypatch.setattr(graph, "is_continuation_of_prior_response", lambda *a: False)
    with TestClient(main.app) as c:
        c.graph = _AnsweringGraph()
        runtime.set_graph(c.graph)
        yield c


def _customer(client, ip=None):
    res = client.post("/api/auth/customer/signup", headers=_ip(ip), json={
        "name": "Dana Smith", "email": f"limits-{next(_counter)}@example.com", "password": "password123"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}", **_ip(ip)}


def _ip(ip):
    return {"X-Forwarded-For": ip} if ip else {}


def _start(client, headers, issue="My Linux agent stopped reporting data"):
    return client.post("/api/chats", json={"issue": issue}, headers=headers)


# ---------------------------------------------------------------------------
# AI turns
# ---------------------------------------------------------------------------
def test_the_21st_ai_turn_in_ten_minutes_gets_429(client):
    headers = _customer(client)
    chat = _start(client, headers).json()
    for i in range(19):  # turns 2..20: chat messages
        res = client.post(f"/api/chats/{chat['session_id']}/messages",
                          json={"message": f"and one more detail, number {i}"}, headers=headers)
        assert res.status_code == 200, (i, res.text)
    calls = client.graph.calls
    res = client.post(f"/api/chats/{chat['session_id']}/messages", json={"message": "one more"}, headers=headers)
    assert res.status_code == 429
    assert int(res.headers["Retry-After"]) == 600  # all 20 turns at the same fake instant
    assert client.graph.calls == calls  # refused before the model ran


def test_every_ai_endpoint_counts_and_is_refused(client, monkeypatch):
    monkeypatch.setenv("AI_USER_MAX_PER_WINDOW", "4")
    headers = _customer(client)
    chat = _start(client, headers).json()                                     # 1
    assert client.post(f"/api/chats/{chat['session_id']}/messages", json={"message": "more detail"},
                       headers=headers).status_code == 200                    # 2
    ticket = client.post("/api/tickets", json={"subject": "Agent", "description": "Agent stopped",
                                               "priority": "medium"}, headers=headers)
    assert ticket.status_code == 200                                          # 3
    ticket_id = ticket.json()["ticket_id"]
    assert client.post(f"/api/tickets/{ticket_id}/reply", json={"message": "still down"},
                       headers=headers).status_code == 200                    # 4
    assert _start(client, headers).status_code == 429
    assert client.post(f"/api/chats/{chat['session_id']}/messages", json={"message": "x"},
                       headers=headers).status_code == 429
    assert client.post("/api/tickets", json={"subject": "s", "description": "d"}, headers=headers).status_code == 429
    assert client.post(f"/api/tickets/{ticket_id}/reply", json={"message": "x"},
                       headers=headers).status_code == 429


def test_the_window_frees_up_and_the_daily_limit_holds(client, clock, monkeypatch):
    monkeypatch.setenv("AI_USER_MAX_PER_WINDOW", "2")
    monkeypatch.setenv("AI_USER_MAX_PER_DAY", "3")
    headers = _customer(client)
    assert _start(client, headers).status_code == 200
    assert _start(client, headers).status_code == 200
    assert _start(client, headers).status_code == 429
    clock.now += 601
    assert _start(client, headers).status_code == 200  # 3 today
    clock.now += 601
    res = _start(client, headers)
    assert res.status_code == 429  # daily limit
    assert 86_400 - 1300 <= int(res.headers["Retry-After"]) <= 86_400
    clock.now += 86_400
    assert _start(client, headers).status_code == 200


def test_customers_have_their_own_limit_but_share_the_ip_limit(client, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    monkeypatch.setenv("AI_USER_MAX_PER_WINDOW", "2")
    monkeypatch.setenv("AI_IP_MAX_PER_WINDOW", "3")
    a, b = _customer(client, "203.0.113.5"), _customer(client, "203.0.113.5")
    other = _customer(client, "198.51.100.9")
    assert [_start(client, a).status_code for _ in range(3)] == [200, 200, 429]
    assert _start(client, b).status_code == 200    # 3rd turn from this IP
    assert _start(client, b).status_code == 429    # IP limit
    assert _start(client, other).status_code == 200


def test_a_refused_turn_is_not_counted(client, monkeypatch):
    monkeypatch.setenv("AI_USER_MAX_PER_WINDOW", "1")
    headers = _customer(client)
    assert _start(client, headers).status_code == 200
    for _ in range(5):
        assert _start(client, headers).status_code == 429
    assert len(rate_limit._ai_users[next(iter(rate_limit._ai_users))]) == 1


def test_staff_endpoints_and_reads_are_not_limited(client, monkeypatch):
    monkeypatch.setenv("AI_USER_MAX_PER_WINDOW", "1")
    headers = _customer(client)
    assert _start(client, headers).status_code == 200
    assert _start(client, headers).status_code == 429
    assert client.get("/api/chats", headers=headers).status_code == 200
    assert client.get("/api/tickets/mine", headers=headers).status_code == 200


# ---------------------------------------------------------------------------
# Sign-up
# ---------------------------------------------------------------------------
def test_signup_is_limited_per_ip(client, clock, monkeypatch):
    monkeypatch.setenv("SIGNUP_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    for _ in range(5):
        _customer(client, "203.0.113.20")
    res = client.post("/api/auth/customer/signup", headers=_ip("203.0.113.20"), json={
        "name": "Sixth", "email": "sixth@example.com", "password": "password123"})
    assert res.status_code == 429 and int(res.headers["Retry-After"]) == 3600
    _customer(client, "203.0.113.21")  # another network
    clock.now += 3601
    _customer(client, "203.0.113.20")


# ---------------------------------------------------------------------------
# Size caps and passwords
# ---------------------------------------------------------------------------
def test_request_size_caps(client):
    headers = _customer(client)
    assert client.post("/api/tickets", json={"subject": "s", "description": "x" * 8001},
                       headers=headers).status_code == 422
    ticket_id = client.post("/api/tickets", json={"subject": "s", "description": "x" * 8000},
                            headers=headers).json()["ticket_id"]
    assert client.post(f"/api/tickets/{ticket_id}/reply", json={"message": "y" * 4001},
                       headers=headers).status_code == 422
    for field, value in (("name", "n" * 121), ("email", "e" * 245 + "@example.com")):
        body = {"name": "Dana", "email": "caps@example.com", "password": "password123", field: value}
        assert client.post("/api/auth/customer/signup", json=body).status_code == 422, field


def test_passwords_over_72_bytes_get_400_not_500(client):
    body = {"name": "Dana", "email": "long@example.com", "password": "p" * 73}
    res = client.post("/api/auth/customer/signup", json=body)
    assert res.status_code == 400 and "72 bytes" in res.json()["detail"]
    body["password"] = "é" * 37  # 74 bytes, 37 characters
    assert client.post("/api/auth/customer/signup", json=body).status_code == 400
    body["password"] = "p" * 72
    assert client.post("/api/auth/customer/signup", json=body).status_code == 200
    # bcrypt compares the first 72 bytes (as it always has): extra bytes
    # don't matter, a different start does, and neither is a 500.
    assert client.post("/api/auth/login", json={"email": "long@example.com", "password": "p" * 200}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "long@example.com", "password": "x" * 200}).status_code == 401
    headers = {"Authorization": f"Bearer {client.post('/api/auth/login', json={'email': 'long@example.com', 'password': 'p' * 72}).json()['token']}"}
    res = client.post("/api/auth/change-password", headers=headers,
                      json={"current_password": "p" * 72, "new_password": "q" * 80})
    assert res.status_code == 400


def test_verify_password_never_raises_on_long_input():
    hashed = auth.hash_password("p" * 72)
    assert auth.verify_password("p" * 72, hashed)
    assert not auth.verify_password("x" * 500, hashed)


# ---------------------------------------------------------------------------
# Transcript cap
# ---------------------------------------------------------------------------
def test_the_transcript_sent_to_the_model_is_the_latest_part_from_a_message_start():
    lines = [f"Customer: message {i} " + "x" * 90 for i in range(300)]
    transcript = "\n".join(lines)
    cut = conversation.latest_transcript(transcript, limit=1000)
    assert len(cut) <= 1000 and cut.startswith("Customer: message ") and cut.endswith(lines[-1])
    assert conversation.latest_transcript("Customer: short", limit=1000) == "Customer: short"
    assert len(conversation.latest_transcript(transcript)) <= conversation.MAX_TRANSCRIPT_CHARS


def test_a_long_chat_sends_only_the_capped_transcript(client, monkeypatch):
    monkeypatch.setattr(conversation, "MAX_TRANSCRIPT_CHARS", 2000)
    monkeypatch.setattr(conversation.latest_transcript, "__defaults__", (2000,))
    headers = _customer(client)
    seen = []
    graph = client.graph
    original = graph.invoke
    graph.invoke = lambda state: (seen.append(state["ticket_text"]), original(state))[1]
    chat = _start(client, headers).json()
    for i in range(8):
        client.post(f"/api/chats/{chat['session_id']}/messages", json={"message": f"detail {i} " + "z" * 600},
                    headers=headers)
    assert max(len(t) for t in seen) <= 2000
    assert seen[-1].rstrip().endswith("z" * 600)
