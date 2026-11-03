"""The options each `aws` subcommand used by scripts/aws_*.sh accepts.

tests/fake_aws_options.json holds them, taken from the SYNOPSIS section
of the real CLI's offline help (`aws <service> <op> help`, no AWS call).
tests/fake_aws_cli.py rejects any other option, like the real CLI, and
tests/test_aws_scripts.py checks every call in the scripts against it.

Regenerate after adding a call to a script (needs the aws CLI installed):

    python3 tests/aws_cli_options.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPTIONS_FILE = Path(__file__).resolve().parent / "fake_aws_options.json"
SCRIPTS = sorted((ROOT / "scripts").glob("aws_*.sh"))
SERVICES = {"amplify", "codebuild", "ec2", "ecr", "ecs", "elbv2", "iam", "logs", "rds", "s3", "s3api",
            "secretsmanager", "sts"}
# Accepted by every subcommand.
GLOBAL_OPTIONS = {"--output", "--query", "--region", "--profile", "--debug", "--no-cli-pager",
                  "--cli-input-json", "--generate-cli-skeleton"}
# Newer than the CLI the file was generated with; aws_up.sh only passes it
# when `help` lists it.
EXTRA = {"ecs create-express-gateway-service": ["--cpu-architecture"]}

_CALL = re.compile(r"\b(?:aws|awst)\s+([a-z0-9]+)\s+([a-z][a-z0-9-]+)((?:[^|;&)]|\|\|)*)")


def extract_calls(paths=SCRIPTS) -> dict[str, set[str]]:
    """{"service op" (or "service wait waiter"): options used} over the scripts."""
    calls: dict[str, set[str]] = {}
    for path in paths:
        text = Path(path).read_text().replace("\\\n", " ")
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            for service, op, rest in _CALL.findall(line):
                if service not in SERVICES:
                    continue
                if op == "wait":
                    waiter, _, rest = rest.strip().partition(" ")
                    op = f"wait {waiter}"
                calls.setdefault(f"{service} {op}", set()).update(
                    re.findall(r"(?<![\w-])--[a-z0-9-]+", rest))
    return calls


def _synopsis(key: str) -> list[str]:
    env = dict(os.environ, AWS_PAGER="", PAGER="cat", MANPAGER="cat")
    out = subprocess.run(["aws", *key.split(), "help"], capture_output=True, text=True, env=env).stdout
    out = re.sub(r".\x08", "", out)  # man-page bold/underline
    match = re.search(r"SYNOPSIS(.*?)\n\s*OPTIONS", out, re.S)
    if not match:
        raise SystemExit(f"no SYNOPSIS in `aws {key} help`")
    return sorted(set(re.findall(r"--[a-z0-9-]+", match.group(1))) - GLOBAL_OPTIONS)


def main() -> int:
    version = subprocess.run(["aws", "--version"], capture_output=True, text=True).stdout.split()[0]
    options = {key: sorted(set(_synopsis(key)) | set(EXTRA.get(key, []))) for key in sorted(extract_calls())}
    data = {"_source": f"{version} help SYNOPSIS, plus {sorted(EXTRA)}", **options}
    fd, tmp = tempfile.mkstemp(dir=OPTIONS_FILE.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, OPTIONS_FILE)
    print(f"wrote {len(options)} subcommands to {OPTIONS_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
