"""Login rate limiting (backend/rate_limit.py): per client IP (every
attempt) and per account (failures only), 429 + Retry-After, the client
IP from X-Forwarded-For only behind a configured number of proxies, and
the counters expiring with their window.

Real temporary DB via FastAPI's TestClient; the clock is a fake.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend import db, main, rate_limit

ADMIN_EMAIL = "rl-admin@example.com"
ADMIN_PASSWORD = "Rl-admin-pass-7731"


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(rate_limit, "_clock", fake)
    return fake


@pytest.fixture()
def client(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("LOGIN_ACCOUNT_MAX_FAILURES", "3")
    monkeypatch.setenv("LOGIN_ACCOUNT_WINDOW_SECONDS", "600")
    monkeypatch.setenv("LOGIN_IP_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("LOGIN_IP_WINDOW_SECONDS", "60")
    with TestClient(main.app) as c:
        yield c


def _login(client, password, email=ADMIN_EMAIL, forwarded=None):
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=headers)


# ---------------------------------------------------------------------------
# Per account
# ---------------------------------------------------------------------------
def test_account_locks_after_max_failures_even_for_the_right_password(client, monkeypatch, caplog):
    monkeypatch.setenv("LOGIN_IP_MAX_ATTEMPTS", "0")  # IP limit off: isolate the account limit
    for _ in range(3):
        assert _login(client, "wrong-password").status_code == 401
    with caplog.at_level(logging.WARNING, logger="backend.rate_limit"):
        res = _login(client, ADMIN_PASSWORD)
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "600"
    assert "Too many sign-in attempts" in res.json()["detail"]
    assert ADMIN_EMAIL not in caplog.text and ADMIN_PASSWORD not in caplog.text


def test_account_key_ignores_case_and_spaces(client, monkeypatch):
    monkeypatch.setenv("LOGIN_IP_MAX_ATTEMPTS", "0")
    for email in (ADMIN_EMAIL.upper(), f"  {ADMIN_EMAIL}", ADMIN_EMAIL):
        assert _login(client, "wrong-password", email=email).status_code == 401
    assert _login(client, ADMIN_PASSWORD).status_code == 429


def test_other_accounts_are_not_affected(client, monkeypatch):
    monkeypatch.setenv("LOGIN_IP_MAX_ATTEMPTS", "0")
    for _ in range(3):
        _login(client, "wrong-password")
    assert _login(client, "wrong-password", email="someone-else@example.com").status_code == 401


def test_success_clears_the_failures(client, monkeypatch):
    monkeypatch.setenv("LOGIN_IP_MAX_ATTEMPTS", "0")
    for _ in range(2):
        _login(client, "wrong-password")
    assert _login(client, ADMIN_PASSWORD).status_code == 200
    for _ in range(2):
        assert _login(client, "wrong-password").status_code == 401
    assert _login(client, ADMIN_PASSWORD).status_code == 200


def test_account_lock_expires_with_its_window(client, clock, monkeypatch):
    monkeypatch.setenv("LOGIN_IP_MAX_ATTEMPTS", "0")
    for _ in range(3):
        _login(client, "wrong-password")
    clock.now += 599
    assert _login(client, ADMIN_PASSWORD).status_code == 429
    clock.now += 1
    assert _login(client, ADMIN_PASSWORD).status_code == 200


# ---------------------------------------------------------------------------
# Per IP
# ---------------------------------------------------------------------------
def test_ip_limit_counts_every_attempt_across_accounts(client, clock):
    for i in range(5):
        assert _login(client, "wrong-password", email=f"user{i}@example.com").status_code == 401
    res = _login(client, ADMIN_PASSWORD)
    assert res.status_code == 429 and res.headers["Retry-After"] == "60"
    clock.now += 60
    assert _login(client, ADMIN_PASSWORD).status_code == 200


def test_forwarded_header_ignored_without_trusted_proxies(client):
    for i in range(5):
        _login(client, "wrong-password", email=f"user{i}@example.com", forwarded=f"10.0.0.{i}")
    assert _login(client, ADMIN_PASSWORD, forwarded="10.9.9.9").status_code == 429


def test_behind_one_proxy_each_client_ip_has_its_own_limit(client, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    for i in range(5):
        _login(client, "wrong-password", email=f"user{i}@example.com", forwarded="203.0.113.7")
    assert _login(client, ADMIN_PASSWORD, forwarded="203.0.113.7").status_code == 429
    # A forged left-hand entry doesn't help: the proxy's own entry is last.
    assert _login(client, ADMIN_PASSWORD, forwarded="198.51.100.1, 203.0.113.7").status_code == 429
    assert _login(client, ADMIN_PASSWORD, forwarded="198.51.100.2").status_code == 200


def test_disabled_means_no_limit(client, monkeypatch):
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ENABLED", "false")
    for _ in range(8):
        assert _login(client, "wrong-password").status_code == 401
    assert _login(client, ADMIN_PASSWORD).status_code == 200


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
def _request(peer="192.0.2.1", forwarded=None):
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


@pytest.mark.parametrize("hops, forwarded, expected", [
    ("0", "203.0.113.7", "192.0.2.1"),
    ("1", "203.0.113.7", "203.0.113.7"),
    ("1", "198.51.100.1, 203.0.113.7", "203.0.113.7"),
    ("2", "198.51.100.1, 203.0.113.7", "198.51.100.1"),
    ("2", "203.0.113.7", "192.0.2.1"),
    ("1", None, "192.0.2.1"),
])
def test_client_ip(monkeypatch, hops, forwarded, expected):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", hops)
    assert rate_limit.client_ip(_request(forwarded=forwarded)) == expected


def test_bad_setting_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("LOGIN_ACCOUNT_MAX_FAILURES", "lots")
    assert rate_limit.setting("LOGIN_ACCOUNT_MAX_FAILURES") == rate_limit.DEFAULTS["LOGIN_ACCOUNT_MAX_FAILURES"]


def test_tracked_keys_stay_bounded(monkeypatch, clock):
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setattr(rate_limit, "MAX_KEYS", 50)
    for i in range(200):
        rate_limit.check_login(f"10.0.{i // 250}.{i % 250}", "a@example.com")
        rate_limit.record_failure(f"user{i}@example.com")
    assert len(rate_limit._ip_attempts) <= 50 and len(rate_limit._account_failures) <= 50
