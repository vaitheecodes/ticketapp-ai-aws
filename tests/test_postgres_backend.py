"""AWS stage A2: the Postgres backend (backend/db_postgres.py) and the
behaviour that has to hold on both databases.

- SQL translation, sqlite3.Row-like rows, settings never leaking secrets:
  pure unit tests, always run.
- The atomic ticket claim: 20 threads claim one ticket at once, exactly
  one wins — on SQLite and on Postgres.
- Pool safety: more concurrent chat turns than pooled connections all
  finish (no deadlock), and an exhausted pool fails fast with a 503.

Postgres cases need the test database (scripts/pg_local.sh start, then
TEST_DATABASE_URL from `scripts/pg_local.sh env`) and skip without it.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from backend import auth, chats, db, db_postgres, graph, main, runtime
from backend.repositories import tickets as tickets_repo
from tests.conftest import postgres_test_url, truncate_postgres

SECRET_PASSWORD = "pw-SECRET-4242"


# ---------------------------------------------------------------------------
# Translation and rows (no database)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sqlite_sql, postgres_sql", [
    ("SELECT * FROM t WHERE a = ? AND b = ?", "SELECT * FROM t WHERE a = %s AND b = %s"),
    ("WHERE lower(c.name) LIKE lower(:q) OR x = :q", "WHERE lower(c.name) LIKE lower(%(q)s) OR x = %(q)s"),
    ("SELECT '10:00 ?' AS s, x FROM t WHERE y = ?", "SELECT '10:00 ?' AS s, x FROM t WHERE y = %s"),
    ("SELECT 'it''s 50%' AS s", "SELECT 'it''s 50%%' AS s"),
    ("ORDER BY created_at DESC, rowid DESC", "ORDER BY created_at DESC, seq DESC"),
    ("SELECT 'rowid' AS label", "SELECT 'rowid' AS label"),
    ("SELECT x::text FROM t", "SELECT x::text FROM t"),
])
def test_sql_translation(sqlite_sql, postgres_sql):
    assert db_postgres.translate(sqlite_sql) == postgres_sql


def test_rows_behave_like_sqlite_rows():
    row = db_postgres.Row(("T-1", 3), {"ticket_id": 0, "n": 1})
    assert row["ticket_id"] == "T-1" and row[1] == 3 and len(row) == 2
    assert dict(row) == {"ticket_id": "T-1", "n": 3} and list(row) == ["T-1", 3]
    with pytest.raises(IndexError):
        row["missing"]


def test_bool_parameters_become_flags():
    assert db_postgres._params((True, False, "x")) == (1, 0, "x")
    assert db_postgres._params({"a": True}) == {"a": 1}


def test_settings_never_expose_the_password(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name, value in {"DB_HOST": "db.internal", "DB_NAME": "ticketapp", "DB_USER": "app",
                        "DB_PASSWORD": SECRET_PASSWORD, "DB_PORT": "5432"}.items():
        monkeypatch.setenv(name, value)
    assert db_postgres.describe() == "db.internal:5432/ticketapp"
    monkeypatch.delenv("DB_HOST")
    with pytest.raises(db_postgres.DatabaseConfigError) as exc:
        db_postgres.conninfo()
    assert "DB_HOST" in str(exc.value) and SECRET_PASSWORD not in str(exc.value)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://app:{SECRET_PASSWORD}@db.internal:5432/ticketapp")
    assert SECRET_PASSWORD not in db_postgres.describe()


def test_default_backend_is_sqlite(monkeypatch):
    monkeypatch.delenv("DB_BACKEND")
    assert db.backend_name() == "sqlite" and not db.is_postgres()


# ---------------------------------------------------------------------------
# Helpers that pick a backend for one test
# ---------------------------------------------------------------------------
def _use_backend(name: str, monkeypatch, tmp_path) -> None:
    if name == "sqlite":
        monkeypatch.setenv("DB_BACKEND", "sqlite")
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    else:
        monkeypatch.setenv("DB_BACKEND", "postgres")
        monkeypatch.setenv("DATABASE_URL", postgres_test_url())
        db_postgres.migrate()
        truncate_postgres()
    db.init_db()


BACKENDS = ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres_only)]


def _customer(conn, name="Claim Customer") -> str:
    customer_id = f"CUST-{uuid.uuid4().hex[:8]}"
    conn.execute("INSERT INTO customers (customer_id, name, email, password_hash) VALUES (?, ?, ?, ?)",
                 (customer_id, name, f"{customer_id.lower()}@example.com", "x"))
    return customer_id


# ---------------------------------------------------------------------------
# Atomic claim
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("backend", BACKENDS)
def test_concurrent_claims_have_exactly_one_winner(backend, monkeypatch, tmp_path):
    _use_backend(backend, monkeypatch, tmp_path)
    monkeypatch.setenv("DB_POOL_MAX", "25")
    threads = 20
    conn = db.get_connection()
    try:
        customer_id = _customer(conn)
        ticket_id = f"TCK-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO tickets (ticket_id, customer_id, channel, ticket_text, route, status, created_at) "
            "VALUES (?, ?, 'email', 'agent down', 'human', 'open', ?)",
            (ticket_id, customer_id, "2026-10-28T10:00:00+00:00"),
        )
        technicians = [f"STAFF-{i:02d}-{uuid.uuid4().hex[:6]}" for i in range(threads)]
        conn.executemany(
            "INSERT INTO staff (staff_id, name, email, password_hash, role) VALUES (?, ?, ?, 'x', 'technician')",
            [(t, f"Tech {t}", f"{t.lower()}@example.com") for t in technicians],
        )
        conn.commit()
    finally:
        conn.close()

    start = threading.Barrier(threads)
    winners, errors = [], []

    def claim(technician_id):
        try:
            c = db.get_connection()
            try:
                start.wait()
                if tickets_repo.claim(c, ticket_id, technician_id):
                    winners.append(technician_id)
                c.commit()
            finally:
                c.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    workers = [threading.Thread(target=claim, args=(t,)) for t in technicians]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=60)

    assert errors == [] and len(winners) == 1
    conn = db.get_connection()
    try:
        assigned = conn.execute("SELECT assigned_technician_id FROM tickets WHERE ticket_id = ?",
                                (ticket_id,)).fetchone()[0]
    finally:
        conn.close()
    assert assigned == winners[0]


# ---------------------------------------------------------------------------
# Pool safety (Postgres only: SQLite has no pool)
# ---------------------------------------------------------------------------
class _SlowGraph:
    """Stands in for the LangGraph pipeline: like the real one it opens its
    own short connections (fetch_customer_history), and it takes a while,
    like a model call."""

    def __init__(self, seconds: float):
        self.seconds = seconds

    def invoke(self, state):
        graph.fetch_customer_history(state)
        time.sleep(self.seconds)
        return {"route": "ai", "category": "AGENT", "confidence": 0.95,
                "ai_response": "Restart the agent service.", "clarification_attempts": 0}


@pytest.mark.postgres_only
def test_more_chat_turns_than_pool_connections_all_finish(monkeypatch, tmp_path):
    """8 concurrent chat turns through a 2-connection pool, each spending
    0.5 s in the 'AI' and opening a nested connection inside it. They all
    finish because no connection is held while the AI works."""
    _use_backend("postgres", monkeypatch, tmp_path)
    monkeypatch.setenv("DB_POOL_MAX", "2")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "5")
    db_postgres.close_pool()
    runtime.set_graph(_SlowGraph(0.5))
    turns = 8
    conn = db.get_connection()
    try:
        customers = [_customer(conn, f"Pool Customer {i}") for i in range(turns)]
        conn.commit()
    finally:
        conn.close()

    start = threading.Barrier(turns)
    results, errors = [], []

    def turn(customer_id):
        try:
            start.wait()
            results.append(chats.start_chat(chats.StartChatRequest(issue="My agent is offline"),
                                            BackgroundTasks(), customer_id=customer_id))
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    began = time.monotonic()
    workers = [threading.Thread(target=turn, args=(c,)) for c in customers]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=60)
    elapsed = time.monotonic() - began

    assert errors == []
    assert len(results) == turns
    assert all(r["ai_error"] is None and r["messages"][-1]["content"] == "Restart the agent service."
               for r in results)
    assert db_postgres.get_pool().get_stats()["pool_max"] == 2
    # The AI waits overlap: far less than 8 x 0.5 s run one after another.
    assert elapsed < turns * 0.5


@pytest.mark.postgres_only
def test_exhausted_pool_answers_503_instead_of_hanging(monkeypatch, tmp_path):
    _use_backend("postgres", monkeypatch, tmp_path)
    monkeypatch.setenv("DB_POOL_MAX", "2")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "1")
    db_postgres.close_pool()
    with TestClient(main.app) as client:
        token = client.post("/api/auth/customer/signup", json={
            "name": "Busy", "email": f"busy-{uuid.uuid4().hex[:6]}@example.com",
            "password": "password123"}).json()["token"]
        held = [db.get_connection(), db.get_connection()]
        try:
            began = time.monotonic()
            res = client.get("/api/chats", headers={"Authorization": f"Bearer {token}"})
            waited = time.monotonic() - began
        finally:
            for c in held:
                c.close()
        assert res.status_code == 503 and res.headers["Retry-After"] == "2"
        assert waited < 5
        assert client.get("/api/chats", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_tests_never_use_a_non_test_database():
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        from psycopg.conninfo import conninfo_to_dict

        assert conninfo_to_dict(url)["dbname"].endswith("_test")
    assert auth  # imported for the app's startup checks
