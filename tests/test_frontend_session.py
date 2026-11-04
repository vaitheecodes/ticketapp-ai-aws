"""The frontend picks the portal from the role inside the token it sends
(frontend/src/lib/tokenRole.js), never from a separately stored role.

A stored role could disagree with the token (another tab signed in as
someone else, or a leftover value), and the admin then got the technician
workspace, whose chat-review, unassigned-ticket and incident calls all
answer 403. Runs the module under Node (skipped without it).
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend import auth

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "frontend" / "src" / "lib" / "tokenRole.js"
API = ROOT / "frontend" / "src" / "api.js"


def _roles(tokens: list[str | None]) -> list[str | None]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = (f"import {{ roleFromToken }} from {json.dumps(MODULE.as_uri())};\n"
              f"console.log(JSON.stringify({json.dumps(tokens)}.map(roleFromToken)));")
    res = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _b64url(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def test_role_comes_from_the_token():
    tokens = [auth.create_token(f"id-{role}", role) for role in ("customer", "technician", "manager", "admin")]
    # base64url characters ("-", "_") and no padding, as real tokens have.
    odd = f"{_b64url({'alg': 'HS256'})}.{_b64url({'sub': '>>>???', 'role': 'admin'})}.sig"
    assert "-" in odd or "_" in odd
    assert _roles([*tokens, odd]) == ["customer", "technician", "manager", "admin", "admin"]


@pytest.mark.parametrize("token", [None, "", "not-a-token", "a.b.c", f"x.{_b64url({'sub': '1'})}.y",
                                   f"x.{_b64url({'role': 7})}.y"])
def test_no_role_without_a_readable_token(token):
    assert _roles([token]) == [None]


def test_api_never_reads_a_stored_role():
    source = API.read_text(encoding="utf-8")
    assert "getItem(LEGACY_ROLE_KEY)" not in source and "setItem(LEGACY_ROLE_KEY" not in source
    assert "return roleFromToken(getToken());" in source
