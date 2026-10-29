"""AWS stage A3: attachments on S3 (backend/storage.py, STORAGE_BACKEND=s3).

boto3 never reaches AWS: the S3 storage gets a fake client. Covers the
same rules as local storage: validation before anything is stored, only
metadata in the database, downloads only through the authorised
endpoint (bytes streamed by the app, never a public or presigned link),
cleanup when an upload fails, and S3 errors mapped without AWS text.
"""

from __future__ import annotations

import io
import logging

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from backend import db, main, runtime, storage
from backend.storage import S3Storage
from tests.test_admin_console import PNG, _Human, _admin, _demo, _raise, _signup

BUCKET = "ticketapp-ai-attachments-000000000000"
AWS_MESSAGE = "User arn:aws:iam::111122223333:user/SECRET-USER is not authorized"


class _FakeS3:
    def __init__(self, fail_put_on: int | None = None, error: str | None = None):
        self.objects: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.fail_put_on, self.error = fail_put_on, error

    def _maybe_fail(self):
        if self.error:
            raise ClientError({"Error": {"Code": self.error, "Message": AWS_MESSAGE}}, "S3")

    def put_object(self, *, Bucket, Key, Body, ContentType, ServerSideEncryption):
        self.calls.append(("put", Bucket, Key))
        self._maybe_fail()
        if self.fail_put_on == len([c for c in self.calls if c[0] == "put"]):
            raise ClientError({"Error": {"Code": "InternalError", "Message": AWS_MESSAGE}}, "PutObject")
        self.objects[Key] = {"Body": Body, "ContentType": ContentType, "SSE": ServerSideEncryption,
                             "Bucket": Bucket}

    def get_object(self, *, Bucket, Key):
        self.calls.append(("get", Bucket, Key))
        self._maybe_fail()
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key]["Body"])}

    def delete_object(self, *, Bucket, Key):
        self.calls.append(("delete", Bucket, Key))
        self.objects.pop(Key, None)


@pytest.fixture()
def s3():
    fake = _FakeS3()
    storage.set_storage(S3Storage(client=fake, bucket=BUCKET))
    return fake


@pytest.fixture()
def client(tmp_path, monkeypatch, s3):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    with TestClient(main.app) as c:
        runtime.set_graph(_Human())
        yield c


def _upload(client, headers, created, files):
    return client.post(f"/api/tickets/{created['ticket_id']}/attachments",
                       data={"message_id": created["message_id"]},
                       files=[("files", (name, data, "application/octet-stream")) for name, data in files],
                       headers=headers)


def _rows():
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM attachments")]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Selection and the client
# ---------------------------------------------------------------------------
def test_local_is_the_default(monkeypatch):
    assert storage.backend_name() == "local"
    assert isinstance(storage.get_storage(), storage.LocalStorage)
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    backend = storage.get_storage()
    assert isinstance(backend, S3Storage) and backend._client is None  # lazy: no AWS client yet


def test_s3_client_uses_the_default_credential_chain(monkeypatch):
    import boto3

    seen = {}

    def fake_client(service, **kwargs):
        seen.update(service=service, **kwargs)
        return _FakeS3()

    monkeypatch.setattr(boto3, "client", fake_client)
    S3Storage(bucket=BUCKET).client()
    assert seen["service"] == "s3" and set(seen) == {"service", "config"}  # no keys passed
    assert seen["config"].region_name == "ap-south-1"


@pytest.mark.parametrize("env, variable", [
    ({"STORAGE_BACKEND": "s3"}, "S3_BUCKET"),
    ({"STORAGE_BACKEND": "ftp"}, "STORAGE_BACKEND"),
])
def test_bad_storage_settings_refuse_to_start(tmp_path, monkeypatch, capsys, env, variable):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=variable):
        with TestClient(main.app):
            pass
    assert f"STARTUP CONFIG ERROR: {variable}" in capsys.readouterr().err


def test_stored_names_cannot_leave_the_prefix():
    backend = S3Storage(client=_FakeS3(), bucket=BUCKET)
    assert backend.key("abc123.png") == "attachments/abc123.png"
    for bad in ("../x.png", "a/b.png", ".hidden", ""):
        with pytest.raises(storage.StorageError):
            backend.key(bad)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
def test_upload_stores_bytes_in_s3_and_only_metadata_in_the_database(client, s3, tmp_path):
    customer, _ = _signup(client)
    created = _raise(client, customer)
    res = _upload(client, customer, created, [("screen.png", PNG), ("agent.log", b"line 1\n")])
    assert res.status_code == 200, res.text

    rows = _rows()
    assert sorted(r["filename"] for r in rows) == ["agent.log", "screen.png"]
    for row in rows:
        stored = s3.objects[f"attachments/{row['stored_name']}"]
        assert stored["Bucket"] == BUCKET and stored["SSE"] == "AES256"
        assert stored["ContentType"] == row["content_type"]
        # Metadata only: no bytes, bucket, URL or key prefix in the row.
        assert not any(isinstance(v, str) and ("s3" in v.lower() or "amazonaws" in v or BUCKET in v)
                       for k, v in row.items() if k not in ("filename",))
    assert not (tmp_path / "uploads").exists() or not any((tmp_path / "uploads").iterdir())


def test_validation_happens_before_anything_is_stored(client, s3):
    customer, _ = _signup(client)
    created = _raise(client, customer)
    res = _upload(client, customer, created, [("ok.png", PNG), ("fake.png", b"MZ not a png")])
    assert res.status_code == 400
    oversized = _upload(client, customer, created, [("big.log", b"a" * (10 * 1024 * 1024 + 1))])
    assert oversized.status_code == 413
    assert s3.calls == [] and _rows() == []


def test_failed_upload_removes_what_was_stored(client, s3):
    customer, _ = _signup(client)
    created = _raise(client, customer)
    s3.fail_put_on = 2
    res = _upload(client, customer, created, [("one.png", PNG), ("two.png", PNG)])
    assert res.status_code == 503
    assert s3.objects == {} and _rows() == []
    assert [c[0] for c in s3.calls] == ["put", "put", "delete"]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def test_download_only_through_the_authorised_endpoint(client, s3):
    customer, _ = _signup(client)
    created = _raise(client, customer)
    (attachment,) = _upload(client, customer, created, [("screen.png", PNG)]).json()
    url = f"/api/tickets/{created['ticket_id']}/attachments/{attachment['attachment_id']}"

    assert client.get(url).status_code == 401
    other_customer, _ = _signup(client)
    assert client.get(url, headers=other_customer).status_code == 403
    gets_before = len([c for c in s3.calls if c[0] == "get"])
    for headers in (customer, _demo(client, "technician"), _admin(client)):
        res = client.get(url, headers=headers)
        assert res.status_code == 200 and res.content == PNG
        assert res.headers["content-type"] == "image/png"
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["cache-control"] == "private, no-store"
        assert res.headers["content-disposition"] == 'attachment; filename="screen.png"'
        # The app streams the bytes: no redirect, no storage link anywhere.
        assert not any("amazonaws" in v or "X-Amz" in v or BUCKET in v for v in res.headers.values())
    # Refused requests never touched S3.
    assert len([c for c in s3.calls if c[0] == "get"]) == gets_before + 3
    detail = client.get(f"/api/tickets/{created['ticket_id']}", headers=customer).text
    assert "amazonaws" not in detail and BUCKET not in detail and "stored_name" not in detail


def test_missing_object_is_404(client, s3):
    customer, _ = _signup(client)
    created = _raise(client, customer)
    (attachment,) = _upload(client, customer, created, [("screen.png", PNG)]).json()
    s3.objects.clear()
    res = client.get(f"/api/tickets/{created['ticket_id']}/attachments/{attachment['attachment_id']}",
                     headers=customer)
    assert res.status_code == 404 and res.json()["detail"] == "Attachment file is missing"


@pytest.mark.parametrize("code, message", [
    ("AccessDenied", "S3 refused access (AccessDenied)"),
    ("NoSuchBucket", "S3 bucket not found"),
    ("SlowDown", "throttled"),
])
def test_s3_errors_answer_503_without_aws_text(client, s3, caplog, code, message):
    customer, _ = _signup(client)
    created = _raise(client, customer)
    s3.error = code
    with caplog.at_level(logging.WARNING):
        res = _upload(client, customer, created, [("screen.png", PNG)])
    assert res.status_code == 503 and res.headers["retry-after"] == "5"
    assert message in caplog.text
    assert "SECRET-USER" not in caplog.text and "111122223333" not in caplog.text
    assert "SECRET-USER" not in res.text and _rows() == []
