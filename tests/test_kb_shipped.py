"""The KB ships with the backend and an empty KB can't go unnoticed.

- The image's file set, built from `git archive HEAD` (what
  scripts/aws_build_image.sh uploads to CodeBuild) and only the paths the
  Dockerfile copies (and .dockerignore lets in), loads the same KB entries
  as this checkout, in production mode, from a read-only directory.
- Startup prints the entry count; production refuses to start with 0
  entries; development starts and says 0.
- /api/meta carries the count (a number, no KB content).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from backend import auth, db, db_postgres, kb_retrieval, main  # noqa: E402
from test_dockerfile import _copied, _in_context  # noqa: E402

STRONG_SECRET = "9f2c71e4b0a85d36" * 3


def _image_files(dest: Path) -> Path:
    """`git archive HEAD`, then only what the Dockerfile copies into /app."""
    try:
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", "HEAD"],
                                 capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    source = dest / "archive"
    source.mkdir()
    subprocess.run(["tar", "-x", "-C", str(source)], input=archive, check=True)
    app = dest / "app"
    for src in _copied():
        if src.startswith("requirements"):  # build stage only
            continue
        assert _in_context(src.rstrip("/")), f".dockerignore keeps {src} out of the build context"
        path = source / src
        assert path.exists(), f"{src} is copied by the Dockerfile but not committed"
        target = app / src
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, target)
        else:
            shutil.copy2(path, target)
    return app


def test_kb_loads_from_the_image_file_set(tmp_path):
    app = _image_files(tmp_path)
    for f in app.rglob("*"):  # root-owned in the image: not writable by the app
        f.chmod(f.stat().st_mode & ~0o222)
    try:
        res = subprocess.run(
            [sys.executable, "-I", "-c",
             "import sys; sys.path.insert(0, '.'); from backend import kb_retrieval as k; "
             "import pathlib; index = k._load_index(); "
             "print(k.KB_PATH.is_relative_to(pathlib.Path.cwd()), len(index.entries) if index else 0)"],
            cwd=app, capture_output=True, text=True, timeout=120,
            env={"PATH": os.environ.get("PATH", ""), "APP_ENV": "production"})
    finally:
        for f in app.rglob("*"):
            f.chmod(f.stat().st_mode | 0o200)
    assert res.returncode == 0, res.stderr
    inside, count = res.stdout.split()
    assert inside == "True"  # read from the app directory, not this checkout
    assert int(count) > 0 and int(count) == kb_retrieval.entry_count()


@pytest.fixture()
def no_kb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(kb_retrieval, "KB_PATH", tmp_path / "missing" / "kb_entries.json")
    kb_retrieval._load_index.cache_clear()
    yield
    kb_retrieval._load_index.cache_clear()


def test_production_refuses_to_start_without_kb_entries(no_kb, monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_EMAIL", "root-admin@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-test-password")
    monkeypatch.setenv("JWT_SECRET", STRONG_SECRET)
    monkeypatch.setattr(auth, "JWT_SECRET", STRONG_SECRET)
    monkeypatch.setattr(db_postgres, "production_problems", lambda: [])
    with pytest.raises(RuntimeError, match="has no KB entries"):
        with TestClient(main.app):
            pass
    out = capsys.readouterr()
    assert "KB: 0 entries loaded" in out.out
    assert "STARTUP CONFIG ERROR" in out.err and "kb_entries.json has no KB entries" in out.err


def test_development_starts_without_kb_and_says_so(no_kb, capsys):
    with TestClient(main.app) as c:
        assert c.get("/api/meta").json()["kb_entries"] == 0
    assert "KB: 0 entries loaded" in capsys.readouterr().out


def test_meta_shows_the_kb_count_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with TestClient(main.app) as c:
        meta = c.get("/api/meta").json()
    count = kb_retrieval.entry_count()
    assert count > 0 and meta["kb_entries"] == count
    assert sorted(meta) == ["categories", "kb_entries", "status_labels"]
    assert f"KB: {count} entries loaded" in capsys.readouterr().out
