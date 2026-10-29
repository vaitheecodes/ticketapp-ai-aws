"""Opt-in check of the S3 attachment storage against the real bucket:
uploads one tiny text object through backend/storage.py, reads it back,
compares, deletes it and confirms it is gone. Not part of pytest.

    AWS_PROFILE=ticketapp S3_BUCKET=<bucket> .venv/bin/python scripts/s3_storage_check.py --yes

Uses S3_BUCKET, S3_PREFIX and AWS_REGION like the app; credentials come
only from the default AWS chain. Prints the object key and sizes, never
credentials.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import storage  # noqa: E402  (also loads .env via backend/__init__)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="really write to and delete from the bucket")
    args = parser.parse_args()
    if not args.yes:
        print("Makes real S3 calls (one small object, deleted at the end). Re-run with --yes.")
        return 2

    backend = storage.S3Storage()
    name = f"storage-check-{uuid.uuid4().hex}.txt"
    data = f"ticketapp storage check {time.time():.0f}\n".encode()
    print(f"bucket: {backend.bucket}   region: {backend.region()}   key: {backend.key(name)}")
    started = time.monotonic()
    try:
        backend.put(name, data, "text/plain")
        print(f"put     ok  {len(data)} bytes")
        echoed = backend.read(name)
        print(f"get     {'ok' if echoed == data else 'MISMATCH'}  {len(echoed)} bytes")
    finally:
        backend.delete(name)
        print("delete  ok")
    try:
        backend.read(name)
        print("gone    NO: the object is still readable")
        return 1
    except storage.StorageNotFound:
        print("gone    ok")
    print(f"total   {time.monotonic() - started:.2f}s")
    return 0 if echoed == data else 1


if __name__ == "__main__":
    sys.exit(main())
