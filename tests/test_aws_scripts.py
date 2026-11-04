"""scripts/aws_up.sh, aws_down.sh and aws_build_image.sh against a fake
`aws` CLI (tests/fake_aws_cli.py): no AWS account is touched.

- up creates what's missing, restores the newest snapshot, and a second
  run changes nothing;
- a restore runs without --manage-master-user-password (the real API
  rejects it for postgres) and a modify then adds the managed secret; a
  re-run skips the modify, and one during the modify waits for it;
- the generated secrets are never printed and never put in the service
  definition (only secret ARNs are);
- down needs a typed "yes", snapshots before deleting anything, deletes
  nothing if the snapshot fails, and keeps the newest KEEP_SNAPSHOTS;
- aws_build_image.sh refuses a dirty tree, stops when the tag is already
  in ECR, creates only what's missing, and on a failed build prints the
  log tail with tokens redacted;
- every script stops at step 1 when the AWS profile is missing, and
  every `aws` call in them uses only options the real subcommand accepts
  (tests/fake_aws_options.json; the fake rejects anything else);
- the smoke test's checks pass and fail on the right responses (incl.
  an empty or unreported KB on /api/meta).
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
sys.path.insert(0, str(ROOT / "tests"))
import aws_cli_options  # noqa: E402
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

        def run(self, script: str, stdin: str = "", scripts: Path = SCRIPTS, **env) -> subprocess.CompletedProcess:
            full_env = {
                "PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path),
                "FAKE_AWS_STATE": str(state), "FAKE_AWS_LOG": str(log),
                "POLL_SECONDS": "0", "IMAGE_TAG": "abc1234",
                "SKIP_FRONTEND": "1", "SKIP_SMOKE": "1", **env,
            }
            return subprocess.run(["bash", str(scripts / script)], input=stdin, capture_output=True,
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
    assert "rds modify-db-instance" not in ops  # created with the managed secret already
    create = next(c for c in fake.calls() if c[1] == "create-db-instance")
    assert "--manage-master-user-password" in create
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


def _rds_calls(fake, op):
    return [c for c in fake.calls() if c[:2] == ["rds", op]]


def test_up_restores_the_newest_snapshot(fake):
    fake.set_state(_existing())
    res = fake.run("aws_up.sh")
    assert res.returncode == 0, res.stderr
    state = fake.state()
    assert state["db"]["from"] == "ticketapp-db-20261102-120000"
    assert "rds create-db-instance" not in fake.ops()
    assert "secretsmanager create-secret" not in fake.ops()
    # Restored without the flag, then one modify turns the managed secret on.
    [restore] = _rds_calls(fake, "restore-db-instance-from-db-snapshot")
    assert "--manage-master-user-password" not in restore
    [modify] = _rds_calls(fake, "modify-db-instance")
    assert {"--manage-master-user-password", "--apply-immediately"} <= set(modify)
    assert fake.ops().index("rds restore-db-instance-from-db-snapshot") < fake.ops().index("rds modify-db-instance")
    assert state["modifications"] == 1 and state["db"]["secret"]
    refs = {x["name"]: x["valueFrom"] for x in state["service"]["container"]["secrets"]}
    assert refs["DB_PASSWORD"] == state["db"]["secret"] + ":password::"


def test_fake_rds_rejects_restore_with_the_managed_password_flag(tmp_path):
    state, log = tmp_path / "state.json", tmp_path / "calls.log"
    state.write_text("{}")
    argv = ["rds", "restore-db-instance-from-db-snapshot", "--db-instance-identifier", "ticketapp-db",
            "--db-snapshot-identifier", "s", "--manage-master-user-password"]
    res = subprocess.run([sys.executable, str(ROOT / "tests" / "fake_aws_cli.py"), *argv], capture_output=True,
                         text=True, env={**os.environ, "FAKE_AWS_STATE": str(state), "FAKE_AWS_LOG": str(log)})
    assert res.returncode == 254
    assert "InvalidParameterValue" in res.stderr and "not valid for engine: postgres" in res.stderr
    assert json.loads(state.read_text()) == {}


def test_up_re_run_after_a_restore_without_secret_modifies_once(fake):
    # As after the old failure, or a run stopped right after the restore.
    fake.set_state(_existing(db={"status": "available", "secret": None, "from": "ticketapp-db-20261102-120000"}))
    res = fake.run("aws_up.sh")
    assert res.returncode == 0, res.stderr
    assert "rds restore-db-instance-from-db-snapshot" not in fake.ops()
    assert len(_rds_calls(fake, "modify-db-instance")) == 1
    first = len(fake.calls())
    assert fake.run("aws_up.sh").returncode == 0
    assert "rds modify-db-instance" not in [f"{c[0]} {c[1]}" for c in fake.calls()[first:]]


def test_up_during_a_modify_waits_for_it_without_another(fake):
    secret = "arn:aws:secretsmanager:ap-south-1:123456789012:secret:rds!db-9"
    fake.set_state(_existing(db={"status": "modifying", "secret": None, "from": None, "next": [
        {"status": "modifying"}, {"status": "modifying"}, {"status": "available", "secret": secret}]}))
    res = fake.run("aws_up.sh")
    assert res.returncode == 0, res.stderr
    assert "rds modify-db-instance" not in fake.ops()
    refs = {x["name"]: x["valueFrom"] for x in fake.state()["service"]["container"]["secrets"]}
    assert refs["DB_PASSWORD"] == secret + ":password::"


def test_up_stops_when_the_secret_never_appears(fake):
    fake.set_state(_existing(db={"status": "available", "secret": None, "from": None}, secret_never=True))
    res = fake.run("aws_up.sh", DB_SECRET_WAIT="1")
    assert res.returncode != 0 and "database secret: still" in res.stderr
    assert not any(op.startswith("ecs create") for op in fake.ops())


def test_down_after_a_restored_up(fake):
    fake.set_state(_existing())
    assert fake.run("aws_up.sh").returncode == 0
    res = fake.run("aws_down.sh", stdin="yes\n")
    assert res.returncode == 0, res.stderr
    state = fake.state()
    assert state["db"] is None and state["service"]["status"] == "INACTIVE"
    assert len(state["snapshots"]) == 2


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


def test_up_says_how_to_build_when_there_is_no_image(fake):
    fake.set_state(_existing(images=[]))
    res = fake.run("aws_up.sh", IMAGE_TAG="")  # this commit's tag; no Docker on the test PATH
    assert res.returncode != 0 and "scripts/aws_build_image.sh" in res.stderr
    assert not any(op.startswith(("rds create", "ecs create")) for op in fake.ops())


@pytest.mark.parametrize("manifest, arch", [
    (None, False),  # one x86_64 manifest, as CodeBuild pushes
    ({"manifests": [{"platform": {"architecture": "amd64"}}, {"platform": {"architecture": "arm64"}}]}, True),
])
def test_up_runs_arm64_only_for_a_multi_arch_image(fake, manifest, arch):
    state = _existing(cli_arch=True)
    if manifest:
        state["manifests"] = {"abc1234": manifest}
    fake.set_state(state)
    res = fake.run("aws_up.sh")
    assert res.returncode == 0, res.stderr
    create = next(c for c in fake.calls() if c[1] == "create-express-gateway-service" and c[2:3] != ["help"])
    assert ("--cpu-architecture" in create) is arch


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
# aws_build_image.sh
# ---------------------------------------------------------------------------
class _Repo(type(Path())):
    """A path that also carries the repository's short commit hash."""
    tag = ""


@pytest.fixture()
def repo(tmp_path):
    """A committed copy of the scripts plus a Dockerfile: its own clean tree."""
    root = _Repo(tmp_path / "repo")
    (root / "scripts").mkdir(parents=True)
    for name in ("aws_lib.sh", "aws_build_image.sh"):
        (root / "scripts" / name).write_text((SCRIPTS / name).read_text())
    (root / "Dockerfile").write_text("FROM python:3.13-slim-bookworm\n")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    root.tag = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    return root


def _build(fake, repo, **env):
    return fake.run("aws_build_image.sh", scripts=repo / "scripts", **env)


def test_build_first_run_creates_everything_and_pushes_this_commit(fake, repo):
    res = _build(fake, repo)
    assert res.returncode == 0, res.stderr
    state, tag = fake.state(), repo.tag
    assert state["repo"] is True and tag in state["images"]
    assert f"ticketapp-api:{tag}" in res.stdout and "Next: run scripts/aws_up.sh" in res.stdout

    bucket = state["bucket"]
    assert bucket["name"] == "ticketapp-build-123456789012" and bucket["region"] == "LocationConstraint=ap-south-1"
    assert "BlockPublicPolicy=true" in bucket["put-public-access-block"]
    assert "AES256" in bucket["put-bucket-encryption"]
    assert json.loads(bucket["put-bucket-lifecycle-configuration"])["Rules"][0]["Expiration"] == {"Days": 7}

    statements = {st["Sid"]: st for st in state["roles"]["ticketapp-codebuild"]["ticketapp-image-build"]["Statement"]}
    assert statements["PushImage"]["Resource"] == "arn:aws:ecr:ap-south-1:123456789012:repository/ticketapp-api"
    assert statements["EcrLogin"]["Action"] == "ecr:GetAuthorizationToken"
    assert all(a.startswith(("s3:Get",)) for a in statements["ReadSource"]["Action"] + statements["SourceBucket"]["Action"])
    assert not any("*" in a for st in statements.values() for a in ([st["Action"]] if isinstance(st["Action"], str) else st["Action"]))

    project = state["project"]
    environment = json.loads(project["environment"])
    assert environment["computeType"] == "BUILD_GENERAL1_SMALL" and environment["privilegedMode"] is True
    assert environment["image"].startswith("aws/codebuild/standard:")
    assert {v["type"] for v in environment["environmentVariables"]} == {"PLAINTEXT"}
    assert {v["name"] for v in environment["environmentVariables"]} == {"REGISTRY", "REPO_URI", "IMAGE_TAG"}
    buildspec = json.loads(project["source"])["buildspec"]
    assert '"docker build -t \\"$REPO_URI:$IMAGE_TAG\\" ."' in buildspec
    assert '"docker push \\"$REPO_URI:$IMAGE_TAG\\""' in buildspec
    assert "--password-stdin" in buildspec
    assert project["service-role"] == "arn:aws:iam::123456789012:role/ticketapp-codebuild"
    assert state["log_groups"]["/aws/codebuild/ticketapp-image-build"]["retention"] == 14

    build = state["builds"][0]
    assert build["tag"] == tag and build["source"] == f"ticketapp-build-123456789012/source/{tag}.zip"
    upload = state["uploads"][f"s3://ticketapp-build-123456789012/source/{tag}.zip"]
    assert upload == {"files": ["Dockerfile", "scripts/", "scripts/aws_build_image.sh", "scripts/aws_lib.sh"],
                      "owner": "123456789012"}
    assert "s3api put-object" in fake.ops() and not any(op.startswith("s3 ") for op in fake.ops())


def test_build_re_run_after_a_failure_creates_nothing_new(fake, repo):
    fake.set_state({"build_result": "FAILED"})
    assert _build(fake, repo).returncode == 1
    first = len(fake.calls())
    state = fake.state()
    state["build_result"] = "SUCCEEDED"
    fake.set_state(state)
    res = _build(fake, repo)
    assert res.returncode == 0, res.stderr
    second = [f"{c[0]} {c[1]}" for c in fake.calls()[first:]]
    assert not [op for op in second if op.split()[1].startswith("create-")]
    assert second.count("codebuild start-build") == 1 and repo.tag in fake.state()["images"]
    # A third run finds the image and changes nothing.
    third_from = len(fake.calls())
    res = _build(fake, repo)
    assert res.returncode == 0 and "nothing to build" in res.stdout
    third = [f"{c[0]} {c[1]}" for c in fake.calls()[third_from:]]
    assert [op for op in third if op.split()[1].startswith(MUTATING)] == ["ecr put-lifecycle-policy"]


@pytest.mark.parametrize("change", ["untracked", "modified"])
def test_build_refuses_a_dirty_tree(fake, repo, change):
    if change == "untracked":
        (repo / "notes.txt").write_text("x")
    else:
        (repo / "Dockerfile").write_text("FROM python:3.13-slim\n")
    res = _build(fake, repo)
    assert res.returncode != 0 and "commit or stash" in res.stderr
    assert fake.calls() == []  # stopped before any AWS call


def test_build_stops_when_the_tag_is_already_in_ecr(fake, repo):
    fake.set_state({"repo": True, "images": [repo.tag]})
    res = _build(fake, repo)
    assert res.returncode == 0, res.stderr
    assert "yes: nothing to build" in res.stdout
    assert not [op for op in fake.ops() if op.startswith(("s3", "codebuild", "iam", "logs"))]


def test_build_failure_prints_the_log_tail_without_tokens(fake, repo):
    token = "eyJwYXlsb2FkIjoiQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0NTY3ODkwYWJjZGVmIn0"
    lines = [f"line {i}" for i in range(1, 41)]
    lines[35] = f"Authorization: {token}"
    lines[36] = "key AKIAABCDEFGHIJKLMNOP used"
    lines[37] = "sha256:" + "a" * 64  # a digest is not a token
    fake.set_state({"build_result": "FAILED", "build_log": lines})
    res = _build(fake, repo)
    assert res.returncode == 1
    assert "build FAILED" in res.stderr and "failed phase: BUILD FAILED" in res.stderr
    assert "line 40" in res.stderr and "line 11" in res.stderr and "line 10\n" not in res.stderr
    assert token not in res.stdout + res.stderr and "AKIAABCDEFGHIJKLMNOP" not in res.stderr
    assert "[redacted]" in res.stderr and "a" * 64 in res.stderr
    assert repo.tag not in fake.state().get("images", [])


# ---------------------------------------------------------------------------
# All scripts: preflight, CLI options
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("script", ["aws_up.sh", "aws_down.sh", "aws_build_image.sh"])
def test_missing_profile_stops_at_preflight(fake, repo, script):
    fake.set_state({"no_profile": True})
    scripts = repo / "scripts" if script == "aws_build_image.sh" else SCRIPTS
    res = fake.run(script, scripts=scripts, AWS_PROFILE="nosuchprofile")
    assert res.returncode != 0
    assert "AWS profile 'nosuchprofile' doesn't exist" in res.stderr and "Failed at step 1" in res.stderr
    assert "region" not in res.stdout  # never reported the profile as fine
    assert fake.ops() == ["sts get-caller-identity"]


def test_preflight_reports_an_authentication_failure(fake):
    fake.set_state({"fail": ["sts get-caller-identity"]})
    res = fake.run("aws_down.sh")
    assert res.returncode != 0 and "can't authenticate (An error occurred (InjectedFailure)" in res.stderr
    assert fake.ops() == ["sts get-caller-identity"]


def test_scripts_use_only_options_the_cli_accepts():
    listed = json.loads((ROOT / "tests" / "fake_aws_options.json").read_text())
    calls = aws_cli_options.extract_calls()
    assert {"s3api put-object", "ecs wait services-stable", "sts get-caller-identity"} <= set(calls)
    missing = sorted(set(calls) - set(listed))
    assert missing == [], f"run tests/aws_cli_options.py for {missing}"
    unsupported = {key: sorted(opts - set(listed[key]) - aws_cli_options.GLOBAL_OPTIONS)
                   for key, opts in calls.items()}
    assert {k: v for k, v in unsupported.items() if v} == {}


@pytest.mark.parametrize("argv, bad", [
    (["s3api", "put-object", "--bucket", "b", "--key", "k", "--body", "f", "--only-show-errors"], "--only-show-errors"),
    (["ecr", "describe-images", "--repository-name", "r", "--image-tag", "x"], "--image-tag"),
    (["ecs", "wait", "services-stable", "--cluster", "c", "--services", "s", "--timeout", "5"], "--timeout"),
    (["ecs", "wait", "services-stable", "--cluster", "c", "--services", "s"], None),
])
def test_fake_cli_rejects_options_the_real_cli_does_not_accept(tmp_path, argv, bad):
    state, log = tmp_path / "state.json", tmp_path / "calls.log"
    state.write_text("{}")
    res = subprocess.run([sys.executable, str(ROOT / "tests" / "fake_aws_cli.py"), *argv], capture_output=True,
                         text=True, env={**os.environ, "FAKE_AWS_STATE": str(state), "FAKE_AWS_LOG": str(log)})
    if bad:
        assert res.returncode == 252 and f"Unknown options: {bad}" in res.stderr
    else:
        assert res.returncode == 0, res.stderr


# ---------------------------------------------------------------------------
# aws_smoke.py
# ---------------------------------------------------------------------------
FRONTEND = "https://main.d123.amplifyapp.com"


def _responses(overrides=None):
    good = {
        ("GET", FRONTEND + "/"): (200, {}, b'<html><div id="root"></div></html>'),
        ("GET", "https://api/health"): (200, {}, b'{"ok": true}'),
        ("GET", "https://api/api/meta"): (200, {}, b'{"categories": ["ALERTS"], "kb_entries": 404}'),
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
    (("GET", "https://api/api/meta"), (200, {}, b'{"categories": ["ALERTS"], "kb_entries": 0}')),
    (("GET", "https://api/api/meta"), (200, {}, b'{"categories": ["ALERTS"]}')),  # an older image
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
    for name in ("aws_up.sh", "aws_down.sh", "aws_build_image.sh"):
        assert os.access(SCRIPTS / name, os.X_OK), name
