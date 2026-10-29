"""Where attachment bytes live. The database keeps only the metadata and a
server-generated `stored_name`; this module stores and reads the bytes.

Selected by STORAGE_BACKEND:
  local  (default) files under db.uploads_dir(), next to the SQLite file.
  s3     a private S3 bucket (S3_BUCKET, AWS_REGION; objects under
         S3_PREFIX, default "attachments/"). Credentials come only from the
         default AWS chain: AWS_PROFILE locally, the service's IAM role when
         deployed. Never keys in code or .env.

Nothing here hands out links: every download goes through the authorised
endpoint (main.download_attachment), which checks access first and then
streams the bytes itself. No public or presigned URL is ever created.
StorageError messages are written by us and never contain AWS error text.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from backend import db


class StorageError(Exception):
    """Storage unreachable or refused the request (safe to log)."""


class StorageConfigError(StorageError):
    """Missing or unusable storage settings: names the variable, never a value."""

    def __init__(self, variable: str, problem: str):
        super().__init__(f"{variable} {problem}")
        self.variable, self.problem = variable, problem


class StorageNotFound(StorageError):
    """No object for this stored_name."""


class Storage(Protocol):
    name: str

    def put(self, stored_name: str, data: bytes, content_type: str) -> None: ...

    def read(self, stored_name: str) -> bytes: ...

    def delete(self, stored_name: str) -> None: ...


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _safe_name(stored_name: str) -> str:
    # stored_name is always ours (uuid hex + extension), but never let a
    # value from the database escape the storage location.
    if not stored_name or "/" in stored_name or "\\" in stored_name or stored_name.startswith("."):
        raise StorageError("Invalid stored attachment name")
    return stored_name


# ---------------------------------------------------------------------------
# Local disk
# ---------------------------------------------------------------------------
class LocalStorage:
    name = "local"

    @staticmethod
    def path(stored_name: str) -> Path:
        return db.uploads_dir() / _safe_name(stored_name)

    def put(self, stored_name, data, content_type):
        self.path(stored_name).write_bytes(data)

    def read(self, stored_name):
        path = self.path(stored_name)
        if not path.is_file():
            raise StorageNotFound("Attachment file is missing")
        return path.read_bytes()

    def delete(self, stored_name):
        self.path(stored_name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Amazon S3
# ---------------------------------------------------------------------------
# S3 error codes -> message. The AWS message itself is never used.
_S3_ERRORS = {
    "AccessDenied": "S3 refused access (AccessDenied): check the IAM policy for S3_BUCKET",
    "NoSuchBucket": "S3 bucket not found (NoSuchBucket): check S3_BUCKET and AWS_REGION",
    "InvalidAccessKeyId": "AWS rejected the credentials (InvalidAccessKeyId)",
    "ExpiredToken": "AWS credentials have expired (ExpiredToken)",
    "SlowDown": "S3 throttled the request (SlowDown)",
}


class S3Storage:
    name = "s3"
    DEFAULT_PREFIX = "attachments/"
    DEFAULT_REGION = "ap-south-1"

    def __init__(self, client=None, bucket: str | None = None, prefix: str | None = None):
        self._client = client
        self.bucket = bucket or _env("S3_BUCKET")
        if not self.bucket:
            raise StorageConfigError("S3_BUCKET", "must be set when STORAGE_BACKEND=s3")
        self.prefix = prefix if prefix is not None else _env("S3_PREFIX", self.DEFAULT_PREFIX)

    @classmethod
    def region(cls) -> str:
        return _env("AWS_REGION") or _env("AWS_DEFAULT_REGION") or cls.DEFAULT_REGION

    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            config = Config(region_name=self.region(), connect_timeout=5, read_timeout=30,
                            retries={"max_attempts": 3, "mode": "standard"})
            try:
                self._client = boto3.client("s3", config=config)
            except Exception as exc:  # noqa: BLE001 — e.g. ProfileNotFound
                raise StorageConfigError("AWS_PROFILE/AWS_REGION", f"could not be used for the S3 client "
                                         f"({type(exc).__name__})") from None
        return self._client

    def key(self, stored_name: str) -> str:
        return f"{self.prefix}{_safe_name(stored_name)}"

    def _call(self, operation: str, **kwargs):
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

        try:
            return getattr(self.client(), operation)(Bucket=self.bucket, **kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NotFound"):
                raise StorageNotFound("Attachment file is missing") from None
            raise StorageError(_S3_ERRORS.get(code, f"S3 error ({code or 'unknown'})")) from None
        except NoCredentialsError:
            raise StorageConfigError("AWS credentials", "not found: set AWS_PROFILE locally, or give "
                                     "the service an IAM role") from None
        except BotoCoreError as exc:
            raise StorageError(f"S3 request failed ({type(exc).__name__})") from None

    def put(self, stored_name, data, content_type):
        self._call("put_object", Key=self.key(stored_name), Body=data, ContentType=content_type,
                   ServerSideEncryption="AES256")

    def read(self, stored_name):
        body = self._call("get_object", Key=self.key(stored_name))["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def delete(self, stored_name):
        self._call("delete_object", Key=self.key(stored_name))


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
_BACKENDS = {"local": LocalStorage, "s3": S3Storage}
_storage: Storage | None = None
_storage_key: tuple | None = None
_pinned: Storage | None = None


def backend_name() -> str:
    name = (_env("STORAGE_BACKEND") or "local").lower()
    if name not in _BACKENDS:
        raise StorageConfigError("STORAGE_BACKEND", "must be local or s3")
    return name


def get_storage() -> Storage:
    global _storage, _storage_key
    if _pinned is not None:
        return _pinned
    key = (backend_name(), _env("S3_BUCKET"), _env("S3_PREFIX"), S3Storage.region())
    if _storage is None or _storage_key != key:
        _storage, _storage_key = _BACKENDS[key[0]](), key
    return _storage


def set_storage(storage: Storage | None) -> None:
    """Tests: always use this storage instance (None: back to STORAGE_BACKEND)."""
    global _pinned
    _pinned = storage


def check_config() -> None:
    """Startup: fail on unusable settings (no AWS call)."""
    get_storage()
