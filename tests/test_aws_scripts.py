"""scripts/aws_up.sh and scripts/aws_down.sh against a fake `aws` CLI
(tests/fake_aws_cli.py): no AWS account is touched.

- up creates what's missing, restores the newest snapshot, and a second
  run changes nothing;
- the generated secrets are never printed and never put in the service
  definition (only secret ARNs are);
- down needs a typed "yes", snapshots before deleting anything, deletes
  nothing if the snapshot fails, and keeps the newest KEEP_SNAPSHOTS;
- the smoke test's checks pass and fail on the right responses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
API_URL = "https://ticketapp-api.ecs.ap-south-1.on.aws/api"
MUTATING = ("create-", "delete-", "put-", "attach-", "authorize-", "restore-", "start-", "modify-", "update-")

sys.path.insert(0, str(SCRIPTS))
import aws_smoke  # noqa: E402


@pytest.fixture()
def fake(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "aws").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests" / "fake_aws_cli.py"}" "$@"\n')
    # The health poll's curl: always 200.
    (bin_dir / "curl").write_text("#!/bin/sh\nprintf 200\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    state, log = tmp_path / "state.json", tmp_path / "calls.log"
    state.write_text("{}")
    log.write_text("")

    class Fake:
        def set_state(self, value: dict) -> None:
            state.write_text(json.dumps(value))

        def state(self) -> dict:
            return json.loads(state.read_text())

        def calls(self) -> list[list[str]]:
            return [json.loads(line) for line in log.read_text().splitlines()]

        def ops(self) -> list[str]:
            return [f"{c[0]} {c[1]}" for c in self.calls() if c[2:3] != ["help"]]

        def mutating(self) -> list[str]:
            return [op for op in self.ops() if op.split()[1].startswith(MUTATING)]

        def run(self, script: str, stdin: str = "", **env) -> subprocess.CompletedProcess:
            full_env = {
                "PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path),
                "FAKE_AWS_STATE": str(state), "FAKE_AWS_LOG": str(log),
                "POLL_SECONDS": "0", "IMAGE_TAG": "abc1234",
                "SKIP_FRONTEND": "1", "SKIP_SMOKE": "1", **env,
            }
            return subprocess.run(["bash", str(SCRIPTS / script)], input=stdin, capture_output=True,
                                  text=True, env=full_env, timeout=120)

    return Fake()


def _existing(**overrides) -> dict:
    """State after a previous aws_down: everything kept, nothing hourly."""
    state = {
        "sgs": {"sg-ticketapp-api": {"name": "ticketapp-api", "from": []},
                "sg-ticketapp-db": {"name": "ticketapp-db", "from": ["sg-ticketapp-api"]}},
        "subnet_group": True,
        "secret": {"arn": "arn:aws:secretsmanager:ap-south-1:123456789012:secret:ticketapp/app-AbC"},
        "repo": True, "images": ["abc1234"],
        "roles": {"ticketapp-ecs-exec": {}, "ticketapp-ecs-infra": {}, "ticketapp-api-task": {}},
        "amplify": {"branch": {"API_URL": API_URL}},
        "snapshots": [
            {"DBSnapshotIdentifier": f"ticketapp-db-2026110{i}-120000", "Status": "available",
             "SnapshotCreateTime": f"2026-11-0{i}T12:00:00Z"} for i in (1, 2)],
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# aws_up.sh
# ---------------------------------------------------------------------------
def test_up_from_nothing_creates_everything_without_printing_secrets(fake):
    fake.set_state({"images": ["abc1234"]})  # pushed by hand (no Docker in tests)
    res = fake.run("aws_up.sh", ADMIN_EMAIL="owner@example.com")
    assert res.returncode == 0, res.stderr
    state = fake.state()
    secret = json.loads(state["secret"]["value"])
    assert set(secret) == {"JWT_SECRET", "ADMIN_PASSWORD", "ADMIN_EMAIL"} and len(secret["JWT_SECRET"]) == 64
    for value in (secret["JWT_SECRET"], secret["ADMIN_PASSWORD"]):
        assert value not in res.stdout + res.stderr
        assert value not in json.dumps(state["service"])  # the service only gets ARNs

    ops = fake.ops()
    assert "rds create-db-instance" in ops and "rds restore-db-instance-from-db-snapshot" not in ops
    create = next(c for c in fake.calls() if c[1] == "create-db-instance")
    assert create[create.index("--engine-version") + 1] == "18.3"
    for flag in ("--no-publicly-accessible", "--manage-master-user-password", "--storage-encrypted"):
        assert flag in create
    assert state["sgs"]["sg-ticketapp-db"]["from"] == ["sg-ticketapp-api"]

    container = state["service"]["container"]
    env = {e["name"]: e["value"] for e in container["environment"]}
    assert env["DB_SSLMODE"] == "require" and env["APP_ENV"] == "production"
    assert env["ALLOWED_ORIGINS"] == "https://main.d123.amplifyapp.com" and env["TRUSTED_PROXY_HOPS"] == "1"
    refs = {s["name"]: s["valueFrom"] for s in container["secrets"]}
    assert refs["DB_PASSWORD"] == "arn:aws:secretsmanager:ap-south-1:123456789012:secret:rds!db-1:password::"
    assert refs["JWT_SECRET"].endswith(":JWT_SECRET::")
    # The old CLI here has no --cpu-architecture: x86_64, no unknown flag.
    assert "cpu-architecture" not in state["service"]["args"]

    exec_policy = state["roles"]["ticketapp-ecs-exec"]["read-ticketapp-secrets"]
    assert exec_policy["Statement"][0]["Resource"] == [state["secret"]["arn"], state["db"]["secret"]]
    task = state["roles"]["ticketapp-api-task"]["ticketapp-api"]["Statement"]
    assert task[1]["Resource"] == "arn:aws:s3:::ticketapp-ai-attachments-123456789012/attachments/*"
    assert "https://ticketapp-api.ecs.ap-south-1.on.aws" in res.stdout


def test_up_needs_admin_email_before_creating_the_secret(fake):
    fake.set_state({"images": ["abc1234"]})
    res = fake.run("aws_up.sh")
    assert res.returncode != 0 and "ADMIN_EMAIL" in res.stderr
    assert "secretsmanager create-secret" not in fake.ops()
    assert not any(op.startswith(("rds create-db-instance", "ecs create")) for op in fake.ops())


def test_up_restores_the_newest_snapshot(fake):
    fake.set_state(_existing())
    res = fake.run("aws_up.sh")
    assert res.returncode == 0, res.stderr
    assert fake.state()["db"]["from"] == "ticketapp-db-20261102-120000"
    assert "rds create-db-instance" not in fake.ops()
    assert "secretsmanager create-secret" not in fake.ops()


def test_up_twice_changes_nothing_the_second_time(fake):
    fake.set_state(_existing())
    assert fake.run("aws_up.sh").returncode == 0
    first = len(fake.calls())
    res = fake.run("aws_up.sh")
    assert res.returncode == 0, res.stderr
    second = [f"{c[0]} {c[1]}" for c in fake.calls()[first:] if c[2:3] != ["help"]]
    changed = [op for op in second if op.split()[1].startswith(("create-", "restore-", "update-express", "authorize-"))]
    assert changed == []
    assert "exists and is current" in res.stdout


def test_up_updates_the_service_when_the_db_secret_changed(fake):
    fake.set_state(_existing())
    assert fake.run("aws_up.sh").returncode == 0
    state = fake.state()
    state["db"]["secret"] += "-new"  # as after a restore
    fake.set_state(state)
    assert fake.run("aws_up.sh").returncode == 0
    assert "ecs update-express-gateway-service" in fake.ops()
    refs = {s["name"]: s["valueFrom"] for s in fake.state()["service"]["container"]["secrets"]}
    assert refs["DB_PASSWORD"].endswith("-new:password::")


# ---------------------------------------------------------------------------
# aws_down.sh
# ---------------------------------------------------------------------------
def _running() -> dict:
    state = _existing()
    state["db"] = {"status": "available", "secret": "arn:rds!db-1"}
    state["service"] = {"status": "ACTIVE", "container": {}}
    return state


@pytest.mark.parametrize("answer", ["no\n", "YES\n", "y\n", ""])
def test_down_without_a_typed_yes_changes_nothing(fake, answer):
    fake.set_state(_running())
    res = fake.run("aws_down.sh", stdin=answer)
    assert res.returncode != 0 and "nothing was changed" in res.stderr
    assert fake.mutating() == []


def test_down_snapshots_first_then_deletes_and_keeps_the_newest(fake):
    fake.set_state(_running())
    res = fake.run("aws_down.sh", stdin="yes\n")
    assert res.returncode == 0, res.stderr
    mutating = fake.mutating()
    assert mutating[0] == "rds create-db-snapshot"
    assert mutating.index("ecs delete-express-gateway-service") < mutating.index("rds delete-db-instance")
    delete_db = next(c for c in fake.calls() if c[1] == "delete-db-instance")
    assert "--skip-final-snapshot" in delete_db and "--delete-automated-backups" in delete_db

    state = fake.state()
    assert state["db"] is None and state["service"]["status"] == "INACTIVE"
    kept = [s["DBSnapshotIdentifier"] for s in state["snapshots"]]
    assert len(kept) == 2 and kept[0] == "ticketapp-db-20261102-120000"  # the oldest was pruned
    assert kept[1].startswith("ticketapp-db-2")
    assert "No. What remains" in res.stdout


def test_down_deletes_nothing_if_the_snapshot_fails(fake):
    state = _running()
    state["fail"] = ["rds create-db-snapshot"]
    fake.set_state(state)
    res = fake.run("aws_down.sh", stdin="yes\n")
    assert res.returncode != 0
    assert not any(op.split()[1].startswith("delete-") for op in fake.ops())
    assert fake.state()["db"] is not None


def test_down_with_nothing_running_does_not_ask(fake):
    fake.set_state(_existing())
    res = fake.run("aws_down.sh")
    assert res.returncode == 0, res.stderr
    assert "Nothing to take down" in res.stdout and fake.mutating() == []


def test_down_is_safe_to_re_run(fake):
    fake.set_state(_running())
    assert fake.run("aws_down.sh", stdin="yes\n").returncode == 0
    before = len(fake.mutating())
    assert fake.run("aws_down.sh").returncode == 0
    assert len(fake.mutating()) == before


# ---------------------------------------------------------------------------
# aws_smoke.py
# ---------------------------------------------------------------------------
FRONTEND = "https://main.d123.amplifyapp.com"


def _responses(overrides=None):
    good = {
        ("GET", FRONTEND + "/"): (200, {}, b'<html><div id="root"></div></html>'),
        ("GET", "https://api/health"): (200, {}, b'{"ok": true}'),
        ("GET", "https://api/api/meta"): (200, {}, b'{"categories": ["ALERTS"]}'),
        ("OPTIONS", "https://api/api/auth/login"): (200, {"Access-Control-Allow-Origin": FRONTEND}, b""),
        ("POST", "https://api/api/auth/login"): (401, {}, b'{"detail": "Invalid"}'),
    }
    good.update(overrides or {})
    return lambda method, url, body=None, headers=None: good[(method, url)]


def test_smoke_passes_on_good_responses(monkeypatch, capsys):
    monkeypatch.setattr(aws_smoke, "_request", _responses())
    assert aws_smoke.main(["--frontend", FRONTEND, "--api", "https://api/api"]) == 0
    assert "All checks passed" in capsys.readouterr().out


@pytest.mark.parametrize("key, response", [
    (("OPTIONS", "https://api/api/auth/login"), (200, {"Access-Control-Allow-Origin": "https://other"}, b"")),
    (("POST", "https://api/api/auth/login"), (503, {}, b"")),
    (("GET", "https://api/health"), (502, {}, b"")),
])
def test_smoke_fails_on_bad_responses(monkeypatch, key, response):
    monkeypatch.setattr(aws_smoke, "_request", _responses({key: response}))
    assert aws_smoke.main(["--frontend", FRONTEND, "--api", "https://api"]) == 1


@pytest.mark.parametrize("given, origin", [
    ("https://x.ecs.ap-south-1.on.aws/api", "https://x.ecs.ap-south-1.on.aws"),
    ("https://x.ecs.ap-south-1.on.aws/api/", "https://x.ecs.ap-south-1.on.aws"),
    ("https://x.ecs.ap-south-1.on.aws", "https://x.ecs.ap-south-1.on.aws"),
    ("https://api", "https://api"),
])
def test_smoke_api_origin(given, origin):
    assert aws_smoke._api_origin(given) == origin


def test_scripts_are_executable():
    for name in ("aws_up.sh", "aws_down.sh"):
        assert os.access(SCRIPTS / name, os.X_OK), name
