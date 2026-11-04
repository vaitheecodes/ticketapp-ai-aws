"""A fake `aws` CLI for tests/test_aws_scripts.py.

Only the calls scripts/aws_up.sh, aws_down.sh and aws_build_image.sh make. State lives in a
JSON file (FAKE_AWS_STATE) and every call is appended to FAKE_AWS_LOG as
a JSON argv list. --query is applied with jmespath and --output text is
formatted like the real CLI (scalars as-is, lists tab-separated, None as
"None"). An unknown resource exits 254 like the real CLI; state["fail"]
lists "service operation" pairs that should fail. An option the real
subcommand doesn't accept (tests/fake_aws_options.json, from the CLI's
own help) exits 252 with the real CLI's "Unknown options" error, and
state["no_profile"] makes every call fail like a missing --profile.

RDS: a restored instance has no managed password secret, and restoring
with --manage-master-user-password fails like the real API does for
postgres. A modify with that flag adds the secret over a few describe
calls (db["next"]: the states still to come, oldest first), first still
"available" without it, as the real instance can read before it starts
modifying.
"""

from __future__ import annotations

import json
import os
import sys

import jmespath

ACCOUNT = "123456789012"
REGION = "ap-south-1"
OPTIONS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_aws_options.json")))
GLOBAL_OPTIONS = {"--output", "--query", "--region", "--profile", "--debug", "--no-cli-pager"}


def unknown_options(service: str, op: str, rest: list[str]) -> list[str] | None:
    """Options the real subcommand would reject; None if it isn't listed at all."""
    key = f"{service} {op}"
    if op == "wait":
        key, rest = f"{key} {rest[0]}", rest[1:]
    if key not in OPTIONS:
        return None
    return [a for a in rest if a.startswith("--") and a not in OPTIONS[key] and a not in GLOBAL_OPTIONS]


def _opts(args: list[str]) -> dict[str, list[str]]:
    opts: dict[str, list[str]] = {}
    key = None
    for arg in args:
        if arg.startswith("--"):
            key = arg[2:]
            opts.setdefault(key, [])
        elif key:
            opts[key].append(arg)
    return opts


def _one(opts, name, default=None):
    values = opts.get(name)
    return values[0] if values else default


def _file(value: str) -> str:
    return open(value[len("file://"):]).read() if value.startswith("file://") else value


class NotFound(Exception):
    pass


class ApiError(Exception):
    """A service error: the real CLI prints it and exits 254."""

    def __init__(self, code: str, op: str, message: str):
        super().__init__(f"An error occurred ({code}) when calling the {op} operation: {message}")


def _new_db_secret(state: dict) -> str:
    n = state["db_generation"] = state.get("db_generation", 0) + 1
    return f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:rds!db-{n}"


def handle(state: dict, service: str, op: str, o: dict) -> object:
    s = state
    if service == "sts" and op == "get-caller-identity":
        return {"Account": ACCOUNT}

    # --- EC2 -------------------------------------------------------------
    if service == "ec2":
        if op == "describe-vpcs":
            return {"Vpcs": [{"VpcId": "vpc-1"}]}
        if op == "describe-subnets":
            return {"Subnets": [{"SubnetId": "subnet-a"}, {"SubnetId": "subnet-b"}]}
        if op == "describe-security-groups":
            groups = s.setdefault("sgs", {})
            if "group-ids" in o:
                gid = _one(o, "group-ids")
                rules = [{"FromPort": 5432, "UserIdGroupPairs": [{"GroupId": src}]}
                         for src in groups.get(gid, {}).get("from", [])]
                return {"SecurityGroups": [{"GroupId": gid, "IpPermissions": rules}]}
            name = next(f.split("Values=")[1] for f in o["filters"] if f.startswith("Name=group-name"))
            hit = [{"GroupId": gid} for gid, g in groups.items() if g["name"] == name]
            return {"SecurityGroups": hit}
        if op == "create-security-group":
            gid = "sg-" + _one(o, "group-name")
            s.setdefault("sgs", {})[gid] = {"name": _one(o, "group-name"), "from": []}
            return {"GroupId": gid}
        if op == "authorize-security-group-ingress":
            s["sgs"][_one(o, "group-id")]["from"].append(_one(o, "source-group"))
            return {}
        if op == "describe-addresses":
            return {"Addresses": s.get("addresses", [])}

    # --- Secrets Manager ---------------------------------------------------
    if service == "secretsmanager":
        secret = s.get("secret")
        if op == "describe-secret":
            if not secret:
                raise NotFound
            return {"ARN": secret["arn"], "DeletedDate": secret.get("deleted")}
        if op == "create-secret":
            s["secret"] = {"arn": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:ticketapp/app-AbC",
                           "value": _file(_one(o, "secret-string"))}
            return {"ARN": s["secret"]["arn"]}
        if op == "restore-secret":
            secret.pop("deleted", None)
            return {}

    # --- ECR ---------------------------------------------------------------
    if service == "ecr":
        if op == "describe-repositories":
            if not s.get("repo"):
                raise NotFound
            return {"repositories": [{}]}
        if op == "create-repository":
            s["repo"] = True
            return {}
        if op == "put-lifecycle-policy":
            return {}
        if op == "describe-images":
            images = s.get("images", [])
            if "image-ids" in o:
                tag = _one(o, "image-ids").split("=", 1)[1]
                if tag not in images:
                    raise NotFound
            return {"imageDetails": [{"imageTags": [t], "imagePushedAt": i} for i, t in enumerate(images)]}
        if op == "batch-get-image":
            tag = _one(o, "image-ids").split("=", 1)[1]
            single = {"schemaVersion": 2, "mediaType": "application/vnd.docker.distribution.manifest.v2+json"}
            manifest = s.get("manifests", {}).get(tag, single)
            return {"images": [{"imageManifest": json.dumps(manifest)}] if tag in s.get("images", []) else []}

    # --- S3 (build bucket) ---------------------------------------------------
    if service == "s3api":
        bucket = s.get("bucket")
        if op == "head-bucket":
            if not bucket:
                raise NotFound
            return {}
        if op == "create-bucket":
            s["bucket"] = {"name": _one(o, "bucket"), "region": _one(o, "create-bucket-configuration")}
            return {}
        if op in ("put-public-access-block", "put-bucket-encryption", "put-bucket-lifecycle-configuration"):
            bucket[op] = o[next(k for k in o if k.endswith("configuration"))][0]
            return {}
        if op == "put-object":
            import zipfile
            key = f"s3://{_one(o, 'bucket')}/{_one(o, 'key')}"
            s.setdefault("uploads", {})[key] = {"files": sorted(zipfile.ZipFile(_one(o, "body")).namelist()),
                                                "owner": _one(o, "expected-bucket-owner")}
            return {"ETag": '"abc"'}

    # --- CodeBuild -----------------------------------------------------------
    if service == "codebuild":
        if op == "batch-get-projects":
            project = s.get("project")
            return {"projects": [{"name": "ticketapp-image-build"}] if project else [],
                    "projectsNotFound": [] if project else _one(o, "names")}
        if op == "create-project":
            s["project"] = {k: (_file(v[0]) if v else None) for k, v in o.items()}
            return {"project": {}}
        if op == "start-build":
            env = dict(p.split("=", 1) for p in _one(o, "environment-variables-override").split(","))
            s.setdefault("builds", []).append({"tag": env["value"], "source": _one(o, "source-location-override"),
                                               "polls": 0})
            return {"build": {"id": f"ticketapp-image-build:{len(s['builds'])}"}}
        if op == "batch-get-builds":
            build = s["builds"][int(_one(o, "ids").split(":")[1]) - 1]
            build["polls"] += 1
            status = "IN_PROGRESS" if build["polls"] == 1 else s.get("build_result", "SUCCEEDED")
            if status == "SUCCEEDED" and build["tag"] not in s.setdefault("images", []):
                s["images"].append(build["tag"])
            failed = [] if status in ("IN_PROGRESS", "SUCCEEDED") else [
                {"phaseType": "BUILD", "phaseStatus": "FAILED"}]
            return {"builds": [{"buildStatus": status, "currentPhase": "BUILD" if status == "IN_PROGRESS" else "COMPLETED",
                                "phases": [{"phaseType": "PROVISIONING", "phaseStatus": "SUCCEEDED"}, *failed],
                                "logs": {"groupName": "/aws/codebuild/ticketapp-image-build", "streamName": "abc"}}]}

    # --- RDS ---------------------------------------------------------------
    if service == "rds":
        db = s.get("db")
        snaps = s.setdefault("snapshots", [])
        if op == "describe-db-subnet-groups":
            if not s.get("subnet_group"):
                raise NotFound
            return {}
        if op == "create-db-subnet-group":
            s["subnet_group"] = True
            return {}
        if op == "describe-db-instances":
            if not db:
                raise NotFound
            if db.get("next"):
                db.update(db["next"].pop(0))
            instance = {"DBInstanceStatus": db["status"],
                        "Endpoint": {"Address": "ticketapp-db.abc.ap-south-1.rds.amazonaws.com"}}
            if db.get("secret"):  # the real API leaves the key out
                instance["MasterUserSecret"] = {"SecretArn": db["secret"]}
            return {"DBInstances": [instance]}
        if op == "restore-db-instance-from-db-snapshot":
            if "manage-master-user-password" in o:
                raise ApiError("InvalidParameterValue", "RestoreDBInstanceFromDBSnapshot",
                               "The parameter ManageMasterUserPassword is not valid for engine: postgres")
            s["db"] = {"status": "creating", "secret": None, "from": _one(o, "db-snapshot-identifier"),
                       "next": [{"status": "available"}]}
            return {}
        if op == "create-db-instance":
            s["db"] = {"status": "creating", "secret": _new_db_secret(s), "from": None,
                       "next": [{"status": "available"}]}
            return {}
        if op == "describe-orderable-db-instance-options":
            return {"OrderableDBInstanceOptions": [{"EngineVersion": v} for v in ("17.6", "18.1", "18.3")]}
        if op == "start-db-instance":
            db["status"] = "available"
            return {}
        if op == "create-db-snapshot":
            snaps.append({"DBSnapshotIdentifier": _one(o, "db-snapshot-identifier"), "Status": "available",
                          "SnapshotCreateTime": f"2026-11-{len(snaps) + 10:02d}T00:00:00Z"})
            return {}
        if op == "describe-db-snapshots":
            if "db-snapshot-identifier" in o:
                hit = [x for x in snaps if x["DBSnapshotIdentifier"] == _one(o, "db-snapshot-identifier")]
                if not hit:
                    raise NotFound
                return {"DBSnapshots": hit}
            return {"DBSnapshots": snaps}
        if op == "delete-db-snapshot":
            s["snapshots"] = [x for x in snaps if x["DBSnapshotIdentifier"] != _one(o, "db-snapshot-identifier")]
            return {}
        if op == "modify-db-instance":
            if "manage-master-user-password" in o:
                if db["status"] != "available":
                    raise ApiError("InvalidDBInstanceState", "ModifyDBInstance",
                                   f"Database instance is not in available state ({db['status']}).")
                s["modifications"] = s.get("modifications", 0) + 1
                done = {"status": "available"} if s.get("secret_never") else {"status": "available",
                                                                               "secret": _new_db_secret(s)}
                db["next"] = [{"status": "available"}, {"status": "modifying"}, {"status": "modifying"}, done]
            return {}
        if op == "delete-db-instance":
            s["db"] = None
            return {}

    # --- IAM ---------------------------------------------------------------
    if service == "iam":
        roles = s.setdefault("roles", {})
        if op == "get-role":
            if _one(o, "role-name") not in roles:
                raise NotFound
            return {}
        if op == "create-role":
            roles[_one(o, "role-name")] = {}
            return {}
        if op == "attach-role-policy":
            return {}
        if op == "put-role-policy":
            roles[_one(o, "role-name")][_one(o, "policy-name")] = json.loads(_file(_one(o, "policy-document")))
            return {}

    # --- Amplify -----------------------------------------------------------
    if service == "amplify":
        app = s.get("amplify")
        if op == "list-apps":
            return {"apps": [{"name": "ticketapp-web", "appId": "d123"}] if app else []}
        if op == "create-app":
            s["amplify"] = {"branch": None}
            return {"app": {"appId": "d123"}}
        if op == "get-branch":
            if not app or app["branch"] is None:
                raise NotFound
            return {"branch": {"environmentVariables": app["branch"]}}
        if op == "create-branch":
            app["branch"] = {}
            return {}
        if op == "update-branch":
            for pair in o["environment-variables"]:
                key, value = pair.split("=", 1)
                app["branch"][key] = value
            return {}

    # --- ECS ---------------------------------------------------------------
    if service == "ecs":
        svc = s.get("service")
        arn = f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/default/ticketapp-api"
        if op == "describe-services":
            if not svc:
                return {"services": []}
            return {"services": [{"serviceArn": arn, "status": svc["status"],
                                  "loadBalancers": [{"targetGroupArn": "arn:tg"}]}]}
        if op == "create-express-gateway-service":
            s["service"] = {"status": "ACTIVE", "container": json.loads(_file(_one(o, "primary-container"))),
                            "args": sorted(o)}
            return {"service": {"serviceArn": arn}}
        if op == "update-express-gateway-service":
            svc["container"] = json.loads(_file(_one(o, "primary-container")))
            return {}
        if op == "describe-express-gateway-service":
            return {"service": {"activeConfigurations": [{
                "primaryContainer": {**svc["container"], "awsLogsConfiguration": {"logGroup": "/aws/ecs/x"}},
                "ingressPaths": [{"endpoint": "ticketapp-api.ecs.ap-south-1.on.aws"}]}]}}
        if op == "delete-express-gateway-service":
            svc["status"] = "INACTIVE"
            return {}

    # --- ELB / logs ----------------------------------------------------------
    if service == "elbv2":
        if op == "describe-target-groups":
            return {"TargetGroups": [{"LoadBalancerArns": ["arn:lb"]}]}
        if op == "modify-load-balancer-attributes":
            return {}
        if op == "describe-load-balancers":
            alive = s.get("service") and s["service"]["status"] == "ACTIVE"
            return {"LoadBalancers": [{"LoadBalancerName": "ecs-express-1"}] if alive else []}
    if service == "logs":
        groups = s.setdefault("log_groups", {})
        if op == "put-retention-policy":
            groups.setdefault(_one(o, "log-group-name"), {})["retention"] = int(_one(o, "retention-in-days"))
            return {}
        if op == "describe-log-groups":
            prefix = _one(o, "log-group-name-prefix")
            return {"logGroups": [{"logGroupName": g} for g in groups if g.startswith(prefix)]}
        if op == "create-log-group":
            groups[_one(o, "log-group-name")] = {}
            return {}
        if op == "get-log-events":
            lines = s.get("build_log", [])[-int(_one(o, "limit")):]
            return {"events": [{"message": line + "\n"} for line in lines]}
    raise SystemExit(f"fake aws: unsupported call {service} {op}")


def _text(value) -> str:
    if value is None:
        return "None"
    if isinstance(value, list):
        if value and all(isinstance(v, list) for v in value):
            return "\n".join(_text(v) for v in value)
        return "\t".join(_text(v) for v in value)
    if isinstance(value, dict):
        return "\t".join(_text(v) for v in value.values())
    return str(value)


def main(argv: list[str]) -> int:
    with open(os.environ["FAKE_AWS_LOG"], "a") as log:
        log.write(json.dumps(argv) + "\n")
    service, op, rest = argv[0], argv[1], argv[2:]
    path = os.environ["FAKE_AWS_STATE"]
    state = json.load(open(path))
    if state.get("no_profile"):
        print(f"\nThe config profile ({os.environ.get('AWS_PROFILE')}) could not be found", file=sys.stderr)
        return 255
    if rest[:1] == ["help"]:
        arch = "  --cpu-architecture\n" if state.get("cli_arch") else ""
        print(f"create-express-gateway-service\n  --service-name\n  --cpu\n{arch}")
        return 0
    if service == "ecr" and op == "get-login-password":
        print("fake-ecr-password")
        return 0
    bad = unknown_options(service, op, rest)
    if bad is None:
        raise SystemExit(f"fake aws: no option list for `{service} {op}`; run tests/aws_cli_options.py")
    if bad:
        print(f"\naws: [ERROR]: Unknown options: {','.join(bad)}", file=sys.stderr)
        return 252
    if op == "wait":
        return 0
    if f"{service} {op}" in state.get("fail", []):
        print(f"An error occurred (InjectedFailure) when calling {op}", file=sys.stderr)
        return 254
    opts = _opts(rest)
    try:
        result = handle(state, service, op, opts)
    except NotFound:
        print(f"An error occurred (NotFound) when calling {op}", file=sys.stderr)
        return 254
    except ApiError as err:
        print(f"\n{err}", file=sys.stderr)
        return 254
    json.dump(state, open(path, "w"))
    if _one(opts, "query"):
        result = jmespath.search(_one(opts, "query"), result)
    print(_text(result) if _one(opts, "output") == "text" else json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
