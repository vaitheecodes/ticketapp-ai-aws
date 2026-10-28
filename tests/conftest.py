"""Shared fixtures.

- backend/graph.py calls load_dotenv() at import, so a developer's .env
  could otherwise leak ADMIN_*/APP_ENV into tests and change which
  accounts get bootstrapped. Every test starts from a known development
  environment; tests that need otherwise set them explicitly.
- Admin settings start from defaults in every test, and real email is
  off (EMAIL_ENABLED unset).
- The AI service provider is disabled: any ai_service call that a test
  hasn't stubbed raises AIError (exercising the graceful fallbacks)
  instead of reaching the real Azure OpenAI deployment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Decided before anything imports backend (which loads .env): only the
# shell selects Postgres for a test run, never a developer's .env.
POSTGRES_RUN = os.environ.get("DB_BACKEND", "").strip().lower() == "postgres"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip() or None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import app_settings  # noqa: E402
from backend.ai_service import provider  # noqa: E402


# ---------------------------------------------------------------------------
# Database backend
# ---------------------------------------------------------------------------
# Default run: SQLite, one temporary file per test (tests set db.DB_PATH).
# `DB_BACKEND=postgres TEST_DATABASE_URL=... pytest` runs the same suite on
# Postgres: migrations once, every table truncated before each test.
# The URL must name a database ending in _test (tests truncate tables), and
# DATABASE_URL is never used, so a real database can't be wiped by mistake.
# If the test database isn't reachable every test is skipped with a reason.
# scripts/pg_local.sh starts one (`scripts/pg_local.sh env` prints the vars).
def postgres_test_url() -> str | None:
    """The test database URL if it is usable, else None (cached)."""
    if not hasattr(postgres_test_url, "_cached"):
        postgres_test_url._cached, postgres_test_url.reason = None, "TEST_DATABASE_URL is not set"
        if TEST_DATABASE_URL:
            from psycopg.conninfo import conninfo_to_dict

            if not conninfo_to_dict(TEST_DATABASE_URL).get("dbname", "").endswith("_test"):
                postgres_test_url.reason = "TEST_DATABASE_URL must name a database ending in _test"
            else:
                import psycopg

                try:
                    psycopg.connect(TEST_DATABASE_URL, connect_timeout=3).close()
                    postgres_test_url._cached = TEST_DATABASE_URL
                except psycopg.Error as exc:
                    postgres_test_url.reason = f"test Postgres not reachable ({type(exc).__name__})"
    return postgres_test_url._cached


def pytest_configure(config):
    config.addinivalue_line("markers", "sqlite_only: tests of SQLite-specific behaviour (legacy migrations)")
    config.addinivalue_line("markers", "postgres_only: needs the test Postgres (skipped when it isn't running)")


def pytest_collection_modifyitems(config, items):
    url = postgres_test_url() if (POSTGRES_RUN or any("postgres_only" in i.keywords for i in items)) else None
    for item in items:
        if POSTGRES_RUN and url is None:
            item.add_marker(pytest.mark.skip(reason=postgres_test_url.reason))
        elif POSTGRES_RUN and "sqlite_only" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="SQLite-specific (legacy in-place migration)"))
        elif "postgres_only" in item.keywords and url is None:
            item.add_marker(pytest.mark.skip(reason=postgres_test_url.reason))


@pytest.fixture(scope="session")
def _postgres_schema():
    from backend import db_postgres

    os.environ["DATABASE_URL"] = postgres_test_url()
    os.environ["DB_BACKEND"] = "postgres"
    try:
        db_postgres.migrate()
        yield
    finally:
        db_postgres.close_pool()


def truncate_postgres() -> None:
    from backend import db_postgres

    conn = db_postgres.get_connection()
    try:
        tables = [t for t in db_postgres.table_names(conn) if t != "schema_migrations"]
        conn.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _database_backend(request, monkeypatch):
    for name in ("DB_POOL_MAX", "DB_POOL_MIN", "DB_POOL_TIMEOUT", "DB_HOST", "DB_PORT", "DB_NAME",
                 "DB_USER", "DB_PASSWORD", "DB_SSLMODE"):
        monkeypatch.delenv(name, raising=False)
    if POSTGRES_RUN:
        request.getfixturevalue("_postgres_schema")
        monkeypatch.setenv("DB_BACKEND", "postgres")
        monkeypatch.setenv("DATABASE_URL", postgres_test_url())
        # TEST_DB_POOL_MAX=1 turns the run into a nested-connection detector:
        # code that holds a connection and asks for another times out.
        for name in ("DB_POOL_MAX", "DB_POOL_TIMEOUT"):
            if os.environ.get(f"TEST_{name}"):
                monkeypatch.setenv(name, os.environ[f"TEST_{name}"])
        truncate_postgres()
    else:
        monkeypatch.setenv("DB_BACKEND", "sqlite")
        monkeypatch.delenv("DATABASE_URL", raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolated_app_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    # Never reach a real model: the mock backend, and no provider credentials.
    monkeypatch.setenv("AI_PROVIDER", "mock")
    for name in ("QUICKML_ENDPOINT_KEY", "QUICKML_ENDPOINT_NAME", "QUICKML_ENVIRONMENT", "QUICKML_API_BASE",
                 "QUICKML_ORG_ID", "CATALYST_ORG_ID", "X_ZOHO_CATALYST_ORG_ID", "X_ZOHO_CATALYST_LISTEN_PORT", "X_ZOHO_CATALYST_CONSOLE_URL", "AI_BASE_URL", "AI_API_KEY",
                 "AI_MODEL", "AI_EMBEDDING_MODEL", "AI_MAX_TOKENS", "AZURE_OPENAI_ENDPOINT",
                 "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT", "BEDROCK_MODEL_ID"):
        monkeypatch.delenv(name, raising=False)
    provider.set_backend(None)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    for name in ("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "INCIDENT_WINDOW_HOURS", "INCIDENT_MIN_TICKETS",
                 "INCIDENT_SIMILARITY", "INCIDENT_ROUTING_CONFIDENCE", "INCIDENT_MEMORY_DAYS",
                 "SMTP_HOST", "SMTP_FROM",
                 "EMAIL_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    # Admin settings are cached per process; never leak between tests.
    app_settings.clear_cache()
    yield
    app_settings.clear_cache()
    provider.set_backend(None)


@pytest.fixture(autouse=True)
def _no_live_ai(monkeypatch):
    def _disabled(*args, **kwargs):
        raise provider.AIError("AI provider disabled in tests")

    monkeypatch.setattr(provider, "complete", _disabled)
