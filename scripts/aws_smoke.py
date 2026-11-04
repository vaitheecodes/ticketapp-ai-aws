"""Smoke test for the hosted demo (docs/AWS_DEPLOY.md, demo-day checklist).

    python3 scripts/aws_smoke.py --frontend https://main.<id>.amplifyapp.com \
        --api https://ticketapp-api.ecs.ap-south-1.on.aws [--admin]

Without --admin it needs no credentials and changes nothing:
  frontend page, /health, /api/meta, CORS preflight from the frontend
  origin, and a sign-in with a random unknown account (401 = the API
  reached the database).
With --admin it asks for the admin email and password (not echoed, never
printed or passed on a command line), signs in, reads /api/auth/me, and
runs one ticket through the live pipeline with the admin simulator
(a Bedrock call; nothing is saved).

Standard library only, so it runs without the project's virtualenv.
Exit code 0 = every check passed.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

TIMEOUT = 60


def _request(method: str, url: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _json(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return None


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}{f'  ({detail})' if detail else ''}")
        self.failed += not ok
        return ok


def public_checks(c: Checks, frontend: str, api: str) -> None:
    status, headers, body = _request("GET", frontend + "/")
    c.check("frontend page", status == 200 and b"<div id=\"root\"" in body, f"HTTP {status}")

    status, _, body = _request("GET", api + "/health")
    c.check("API /health", status == 200 and _json(body) == {"ok": True}, f"HTTP {status}")

    status, _, body = _request("GET", api + "/api/meta")
    meta = _json(body) or {}
    categories = meta.get("categories") or []
    c.check("API /api/meta", status == 200 and len(categories) > 0, f"HTTP {status}, {len(categories)} categories")
    kb = meta.get("kb_entries")
    c.check("KB loaded", isinstance(kb, int) and kb > 0, f"{kb if kb is not None else 'no'} KB entries")

    status, headers, _ = _request("OPTIONS", api + "/api/auth/login", headers={
        "Origin": frontend, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    allowed = {k.lower(): v for k, v in headers.items()}.get("access-control-allow-origin")
    c.check("CORS allows the frontend origin", status == 200 and allowed == frontend,
            f"HTTP {status}, allow-origin {allowed or 'missing'}")

    unknown = f"smoke-{secrets.token_hex(4)}@example.com"
    status, _, _ = _request("POST", api + "/api/auth/login", {"email": unknown, "password": secrets.token_hex(8)})
    c.check("sign-in reaches the database (unknown account -> 401)", status == 401, f"HTTP {status}")


def admin_checks(c: Checks, api: str) -> None:
    email = os.environ.get("SMOKE_ADMIN_EMAIL") or input("  admin email: ").strip()
    password = getpass.getpass("  admin password (not shown): ")
    status, _, body = _request("POST", api + "/api/auth/login", {"email": email, "password": password})
    del password
    token = (_json(body) or {}).get("token") or (_json(body) or {}).get("access_token")
    if not c.check("admin sign-in", status == 200 and bool(token), f"HTTP {status}"):
        return
    auth = {"Authorization": f"Bearer {token}"}

    status, _, body = _request("GET", api + "/api/auth/me", headers=auth)
    me = _json(body) or {}
    c.check("/api/auth/me", status == 200 and me.get("role") == "admin", f"HTTP {status}")
    if me.get("must_change_password"):
        print("  NOTE  the admin must change the temporary password in the app before other admin pages work")
        return

    status, _, body = _request("POST", api + "/api/admin/simulate-ticket", {
        "ticket_text": "My website monitor shows the site as down but it loads fine in my browser.",
        "channel": "chat"}, headers=auth)
    result = _json(body) or {}
    c.check("AI pipeline (Bedrock) via the admin simulator", status == 200 and bool(result.get("category")),
            f"HTTP {status}, category {result.get('category')}, route {result.get('route')}")


def _api_origin(url: str) -> str:
    """The API origin: a trailing /api path (VITE_API_URL's form) is dropped."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if path.endswith("/api"):
        path = path[: -len("/api")]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--frontend", required=True, help="the Amplify site, https://main.<id>.amplifyapp.com")
    parser.add_argument("--api", required=True, help="the API origin (no /api suffix)")
    parser.add_argument("--admin", action="store_true", help="also sign in as the admin (asks for the password)")
    args = parser.parse_args(argv)
    frontend, api = args.frontend.rstrip("/"), _api_origin(args.api)

    c = Checks()
    print(f"Smoke test: {frontend} -> {api}")
    public_checks(c, frontend, api)
    if args.admin:
        admin_checks(c, api)
    print("All checks passed." if not c.failed else f"{c.failed} check(s) failed.")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
