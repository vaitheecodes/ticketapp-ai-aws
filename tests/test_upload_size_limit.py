"""The attachment upload is refused before its body is read when it
declares more than main.MAX_UPLOAD_REQUEST_BYTES (413) or no length at all
(411): FastAPI would otherwise spool the whole multipart body to disk
before the handler counts the files (main._UploadSizeLimit).

Called through the full ASGI app (CORS included); `receive` fails the
test if the body is ever read.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend import main

PATH = "/api/tickets/TCK-12345678/attachments"


async def _never_read():
    raise AssertionError("the request body was read")


async def _empty_body():
    return {"type": "http.request", "body": b"", "more_body": False}


def _call(path=PATH, method="POST", length: str | None = None, origin: str | None = None,
          receive=_never_read):
    headers = [(b"content-type", b"multipart/form-data; boundary=x")]
    if length is not None:
        headers.append((b"content-length", length.encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": headers, "client": ("192.0.2.1", 1234), "server": ("test", 80)}
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(main.app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, json.loads(body or b"{}")


@pytest.mark.parametrize("length", [str(main.MAX_UPLOAD_REQUEST_BYTES + 1), str(10 ** 12), "abc", "-1"])
def test_an_oversized_or_bad_length_is_refused_before_the_body_is_read(length):
    status, _, body = _call(length=length)
    assert status == 413 and "too large" in body["detail"]


def test_no_length_is_refused_before_the_body_is_read():
    status, _, body = _call(length=None)
    assert status == 411


def test_the_refusal_still_has_cors_headers():
    status, headers, _ = _call(length=str(10 ** 9), origin="http://localhost:5173")
    assert status == 413 and headers.get("access-control-allow-origin") == "http://localhost:5173"


@pytest.mark.parametrize("path, method", [
    ("/api/tickets/TCK-1/reply", "POST"),       # another route: not this middleware's business
    (PATH, "GET"),
])
def test_other_requests_pass_through(path, method):
    status, _, _ = _call(path=path, method=method, length=str(10 ** 12))
    assert status not in (411, 413)


def test_a_declared_size_within_the_limit_reaches_the_app():
    # The app itself answers (its form parsing or auth), not the middleware.
    status, _, _ = _call(length=str(main.MAX_UPLOAD_REQUEST_BYTES), receive=_empty_body)
    assert status not in (411, 413)
