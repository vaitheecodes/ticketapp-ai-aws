"""Shared fixtures. backend/graph.py calls load_dotenv() at import, so a
developer's .env could otherwise leak ADMIN_*/APP_ENV into tests and
change which accounts get bootstrapped. Every test starts from a known
development environment; tests that need otherwise set them explicitly."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_app_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
