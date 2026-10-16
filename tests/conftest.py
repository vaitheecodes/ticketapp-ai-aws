"""Shared fixtures.

- backend/graph.py calls load_dotenv() at import, so a developer's .env
  could otherwise leak ADMIN_*/APP_ENV into tests and change which
  accounts get bootstrapped. Every test starts from a known development
  environment; tests that need otherwise set them explicitly.
- The AI service provider is disabled: any ai_service call that a test
  hasn't stubbed raises AIError (exercising the graceful fallbacks)
  instead of reaching the real Azure OpenAI deployment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.ai_service import provider  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_app_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    for name in ("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "INCIDENT_WINDOW_HOURS", "INCIDENT_MIN_TICKETS",
                 "INCIDENT_SIMILARITY", "INCIDENT_ROUTING_CONFIDENCE", "SMTP_HOST", "SMTP_FROM"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_live_ai(monkeypatch):
    def _disabled(*args, **kwargs):
        raise provider.AIError("AI provider disabled in tests")

    monkeypatch.setattr(provider, "complete", _disabled)
