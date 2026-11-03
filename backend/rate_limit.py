"""Login rate limiting: per client IP and per account.

- Per IP: every login attempt counts (LOGIN_IP_MAX_ATTEMPTS within
  LOGIN_IP_WINDOW_SECONDS), so one address can't spray many accounts.
- Per account: only failed attempts count (LOGIN_ACCOUNT_MAX_FAILURES
  within LOGIN_ACCOUNT_WINDOW_SECONDS); a successful login clears them.
  While an account is over the limit even the right password gets 429,
  otherwise a guesser would still learn when a guess was right.

Both are checked before the password is verified. A limited attempt gets
429 with Retry-After and is logged without the email or password.
LOGIN_RATE_LIMIT_ENABLED=false turns it off.

Counters live in this process's memory: each instance (ECS task) keeps
its own, and a restart clears them. That is enough for one or two tasks;
more instances need a shared store (the database or Redis).

Client IP: the socket peer, unless TRUSTED_PROXY_HOPS says how many
proxies we sit behind (1 behind an AWS ALB). Then the IP is taken that
many entries from the right of X-Forwarded-For: each proxy appends the
address it saw, so entries further left are client-supplied and can be
forged. With 0 (the default) X-Forwarded-For is ignored.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import deque

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

DEFAULTS = {
    "LOGIN_IP_MAX_ATTEMPTS": 30,
    "LOGIN_IP_WINDOW_SECONDS": 300,
    "LOGIN_ACCOUNT_MAX_FAILURES": 5,
    "LOGIN_ACCOUNT_WINDOW_SECONDS": 900,
    "TRUSTED_PROXY_HOPS": 0,
}
# Bound on tracked keys; idle ones are pruned first.
MAX_KEYS = 10_000

_clock = time.monotonic
_lock = threading.Lock()
_ip_attempts: dict[str, deque] = {}
_account_failures: dict[str, deque] = {}


def setting(name: str) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else DEFAULTS[name]
    except ValueError:
        logger.warning("%s is not a whole number; using %d", name, DEFAULTS[name])
        value = DEFAULTS[name]
    return max(value, 0)


def enabled() -> bool:
    return (os.environ.get("LOGIN_RATE_LIMIT_ENABLED") or "true").strip().lower() not in ("false", "0", "no")


def reset() -> None:
    with _lock:
        _ip_attempts.clear()
        _account_failures.clear()


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    hops = setting("TRUSTED_PROXY_HOPS")
    if not hops:
        return peer
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    return forwarded[-hops] if len(forwarded) >= hops else peer


def _account_key(email: str) -> str:
    return email.strip().lower()


def _recent(store: dict[str, deque], key: str, window: int, now: float) -> deque:
    """The key's timestamps inside the window (a fresh, unstored deque
    for an unknown key, so a lookup never adds one)."""
    times = store.get(key, deque())
    while times and times[0] <= now - window:
        times.popleft()
    return times


def _prune(store: dict[str, deque], window: int, now: float) -> None:
    if len(store) < MAX_KEYS:
        return
    for key in [k for k, times in store.items() if not times or times[-1] <= now - window]:
        del store[key]
    while len(store) >= MAX_KEYS:  # all active: drop the oldest-inserted
        del store[next(iter(store))]


def _too_many(reason: str, retry_after: float) -> HTTPException:
    logger.warning("Login rate limited (%s)", reason)
    return HTTPException(status_code=429, detail="Too many sign-in attempts. Please wait and try again.",
                         headers={"Retry-After": str(max(1, math.ceil(retry_after)))})


def check_login(ip: str, email: str) -> None:
    """Raise 429 if this IP or account is over its limit; otherwise count
    the attempt against the IP."""
    if not enabled():
        return
    now = _clock()
    ip_max, ip_window = setting("LOGIN_IP_MAX_ATTEMPTS"), setting("LOGIN_IP_WINDOW_SECONDS")
    acct_max, acct_window = setting("LOGIN_ACCOUNT_MAX_FAILURES"), setting("LOGIN_ACCOUNT_WINDOW_SECONDS")
    with _lock:
        failures = _recent(_account_failures, _account_key(email), acct_window, now)
        if acct_max and len(failures) >= acct_max:
            raise _too_many("account", failures[0] + acct_window - now)
        attempts = _recent(_ip_attempts, ip, ip_window, now)
        if ip_max and len(attempts) >= ip_max:
            raise _too_many("ip", attempts[0] + ip_window - now)
        _prune(_ip_attempts, ip_window, now)
        attempts.append(now)
        _ip_attempts[ip] = attempts


def record_failure(email: str) -> None:
    if not enabled():
        return
    now = _clock()
    window = setting("LOGIN_ACCOUNT_WINDOW_SECONDS")
    with _lock:
        _prune(_account_failures, window, now)
        key = _account_key(email)
        failures = _recent(_account_failures, key, window, now)
        failures.append(now)
        _account_failures[key] = failures


def record_success(email: str) -> None:
    with _lock:
        _account_failures.pop(_account_key(email), None)
