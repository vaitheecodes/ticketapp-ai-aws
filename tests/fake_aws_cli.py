"""A fake `aws` CLI for tests/test_aws_scripts.py.

Only the calls scripts/aws_up.sh and aws_down.sh make. State lives in a
JSON file (FAKE_AWS_STATE) and every call is appended to FAKE_AWS_LOG as
a JSON argv list. --query is applied with jmespath and --output text is
formatted like the real CLI (scalars as-is, lists tab-separated, None as
"None"). An unknown resource exits 254 like the real CLI; state["fail"]
lists "service operation" pairs that should fail.
"""

from __future__ import annotations

import json
import os
import sys

import jmespath

ACCOUNT = "123456789012"
REGION = "ap-south-1"


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
            return {"DBInstances": [{
                "DBInstanceStatus": db["status"],
                "Endpoint": {"Address": "ticketapp-db.abc.ap-south-1.rds.amazonaws.com"},
                "MasterUserSecret": {"SecretArn": db["secret"]}}]}
        if op in ("create-db-instance", "restore-db-instance-from-db-snapshot"):
            n = s["db_generation"] = s.get("db_generation", 0) + 1
            s["db"] = {"status": "available", "secret": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:rds!db-{n}",
                       "from": _one(o, "db-snapshot-identifier")}
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
    if service == "logs" and op == "put-retention-policy":
        return {}
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
    if rest[:1] == ["help"]:
        print("create-express-gateway-service\n  --service-name\n  --cpu\n")
        return 0
    if service == "ecr" and op == "get-login-password":
        print("fake-ecr-password")
        return 0
    if op == "wait":
        return 0
    path = os.environ["FAKE_AWS_STATE"]
    state = json.load(open(path))
    if f"{service} {op}" in state.get("fail", []):
        print(f"An error occurred (InjectedFailure) when calling {op}", file=sys.stderr)
        return 254
    opts = _opts(rest)
    try:
        result = handle(state, service, op, opts)
    except NotFound:
        print(f"An error occurred (NotFound) when calling {op}", file=sys.stderr)
        return 254
    json.dump(state, open(path, "w"))
    if _one(opts, "query"):
        result = jmespath.search(_one(opts, "query"), result)
    print(_text(result) if _one(opts, "output") == "text" else json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
