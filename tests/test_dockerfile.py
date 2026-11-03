"""The backend image (Dockerfile + .dockerignore) carries every file the
backend reads at runtime, nothing secret, runs as a non-root user, and
health-checks /health. Static checks: Docker isn't needed to run them.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from backend import db_postgres, kb_new_entries, kb_registry, kb_retrieval

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text()
IGNORE = [line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()
          if line.strip() and not line.startswith("#")]


def _copied() -> list[str]:
    """Sources of the final stage's COPY lines (not --from=build)."""
    final = DOCKERFILE.split("\nFROM ", 2)[-1]
    sources = []
    for line in final.splitlines():
        if line.startswith("COPY ") and "--from=" not in line:
            sources += line.split()[1:-1]
    return sources


def _in_context(path: str) -> bool:
    """Whether .dockerignore lets `path` into the build context (last
    matching rule wins; `*` excludes everything first)."""
    included = True
    for rule in IGNORE:
        negate = rule.startswith("!")
        pattern = rule.lstrip("!").rstrip("/")
        pattern = pattern[3:] if pattern.startswith("**/") else pattern
        target = path.rstrip("/")
        if fnmatch.fnmatch(target, pattern) or target.startswith(pattern + "/") \
                or fnmatch.fnmatch(target.rsplit("/", 1)[-1], pattern):
            included = negate
    return included


def test_runtime_files_are_copied_and_allowed():
    copied = _copied()
    for path in (kb_retrieval.KB_PATH, kb_new_entries.NEW_ENTRIES_PATH, kb_registry.REGISTRY_PATH,
                 db_postgres.MIGRATIONS_DIR):
        rel = path.relative_to(ROOT).as_posix()
        assert any(rel == c.rstrip("/") or rel.startswith(c.rstrip("/") + "/") for c in copied), rel
    for source in copied:
        assert (ROOT / source).exists(), source
        assert _in_context(source), source
    assert _in_context("requirements-runtime.txt")


def test_secrets_and_local_state_stay_out_of_the_context():
    for path in (".env", "backend/.env", "frontend/.env", "backend/ticketapp.db", "backend/uploads/x.png",
                 "backend/__pycache__/main.cpython-313.pyc", "data/raw/Site24x7.csv", "tests/conftest.py"):
        assert not _in_context(path), path


def test_runs_as_non_root_with_a_health_check():
    assert re.search(r"^USER app$", DOCKERFILE, re.M)
    assert "useradd --system --uid 10001" in DOCKERFILE
    assert "HEALTHCHECK" in DOCKERFILE and "/health" in DOCKERFILE
    # COPY keeps the build machine's modes: a 0600 file would be unreadable.
    assert "chmod -R a+rX,go-w /app" in DOCKERFILE
    assert DOCKERFILE.index("chmod -R a+rX") < DOCKERFILE.index("USER app")
    assert "--only-binary=:all: -r /tmp/requirements-runtime.txt" in DOCKERFILE


def test_runtime_requirements_leave_out_dev_libraries():
    names = {re.split(r"[<>=\[ ]", line, 1)[0].lower()
             for line in (ROOT / "requirements-runtime.txt").read_text().splitlines()
             if line.strip() and not line.startswith("#")}
    assert not names & {"pandas", "pyarrow", "duckdb", "pytest", "streamlit", "matplotlib", "openai", "faker"}
    assert {"fastapi", "uvicorn", "boto3", "psycopg"} <= names
