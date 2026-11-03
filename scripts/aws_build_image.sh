#!/usr/bin/env bash
# Build the backend image in AWS CodeBuild and push it to ECR, for a Mac
# without Docker (docs/AWS_DEPLOY.md, "Building the image without Docker").
# scripts/aws_up.sh then finds it: the tag is this commit's short hash.
#
#   1. needs a clean git tree (ECR tags are immutable: the tag must name
#      exactly what's committed);
#   2. ECR repository ticketapp-api, created if missing (same settings as
#      aws_up.sh, shared code in aws_lib.sh);
#   3. stops here (exit 0) if this commit's tag is already in ECR;
#   4. S3 bucket ticketapp-build-<account>: private, encrypted, objects
#      deleted after 7 days;
#   5. IAM role ticketapp-codebuild: logs, read the source zips, push to
#      ticketapp-api only;
#   6. CodeBuild project ticketapp-image-build (x86_64, small, privileged
#      for docker build), logs kept 14 days;
#   7. uploads `git archive HEAD`, starts the build and waits; on failure
#      prints the last 30 log lines (long tokens redacted) and exits 1.
#
# Idempotent: creates only what's missing; re-run it after a failure.
# Cost: ~$0.005 per build minute (a build takes ~3-5 min), the first 100
# minutes a month free; the bucket and logs are cents. The build gets no
# secrets: its only variables are the registry, repository and tag, and
# the ECR login token goes through a pipe, never a log line.
#
# Settings (environment): AWS_PROFILE, AWS_REGION (as aws_up.sh),
#   BUILD_IMAGE  CodeBuild image, default aws/codebuild/standard:7.0

source "$(dirname "$0")/aws_lib.sh"
ROOT=$(cd "$(dirname "$0")/.." && pwd)

step "Checking the AWS identity and the git tree"
need git
[ -z "$(git -C "$ROOT" status --porcelain)" ] \
  || die "uncommitted or untracked files: commit or stash them first (the image tag is the commit hash, and an ECR tag can't be overwritten)"
preflight
TAG=$(git -C "$ROOT" rev-parse --short HEAD)
info "commit $TAG"
BUILD_BUCKET=$NAME-build-$ACCOUNT_ID
REGISTRY=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
REPO_URI=$REGISTRY/$SERVICE
LOG_GROUP=/aws/codebuild/$BUILD_PROJECT
BUILD_IMAGE=${BUILD_IMAGE:-aws/codebuild/standard:7.0}

# ---------------------------------------------------------------------------
step "ECR repository $SERVICE: storage \$0.10/GB-month"
ensure_ecr_repo

# ---------------------------------------------------------------------------
step "Is image $TAG already in ECR?"
if has_image "$TAG"; then
  info "yes: nothing to build"
  info "Next: run scripts/aws_up.sh"
  exit 0
fi
info "no: building it in CodeBuild"

# ---------------------------------------------------------------------------
step "Build bucket s3://$BUILD_BUCKET (private, encrypted, objects expire after 7 days): cents"
if aws s3api head-bucket --bucket "$BUILD_BUCKET" --expected-bucket-owner "$ACCOUNT_ID" >/dev/null 2>&1; then
  info "exists"
else
  if [ "$AWS_REGION" = us-east-1 ]; then
    aws s3api create-bucket --bucket "$BUILD_BUCKET" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUILD_BUCKET" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
  fi
  info "created"
fi
# (Re)applied every run, so a run that stopped right after create-bucket
# is completed by the next one.
aws s3api put-public-access-block --bucket "$BUILD_BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true >/dev/null
aws s3api put-bucket-encryption --bucket "$BUILD_BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null
aws s3api put-bucket-lifecycle-configuration --bucket "$BUILD_BUCKET" --lifecycle-configuration \
  '{"Rules":[{"ID":"expire-after-7-days","Status":"Enabled","Filter":{"Prefix":""},"Expiration":{"Days":7},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":1}}]}' >/dev/null
info "public access blocked, SSE-S3 encryption, 7-day expiry"

# ---------------------------------------------------------------------------
step "CodeBuild role $BUILD_ROLE (free)"
cat > "$TMP_DIR/codebuild-trust.json" <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"codebuild.amazonaws.com"},
 "Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"$ACCOUNT_ID"}}}]}
JSON
ROLE_CREATED=0
if aws iam get-role --role-name "$BUILD_ROLE" >/dev/null 2>&1; then
  info "exists"
else
  aws iam create-role --role-name "$BUILD_ROLE" \
    --assume-role-policy-document "file://$TMP_DIR/codebuild-trust.json" >/dev/null
  ROLE_CREATED=1
  info "created"
fi
# Least privilege, rewritten each run: write its own log group, read the
# source zips, push to this one repository (GetAuthorizationToken has no
# resource-level form).
cat > "$TMP_DIR/codebuild-policy.json" <<JSON
{"Version":"2012-10-17","Statement":[
 {"Sid":"Logs","Effect":"Allow","Action":["logs:CreateLogStream","logs:PutLogEvents"],
  "Resource":"arn:aws:logs:$AWS_REGION:$ACCOUNT_ID:log-group:$LOG_GROUP:*"},
 {"Sid":"ReadSource","Effect":"Allow","Action":["s3:GetObject","s3:GetObjectVersion"],
  "Resource":"arn:aws:s3:::$BUILD_BUCKET/source/*"},
 {"Sid":"SourceBucket","Effect":"Allow","Action":["s3:GetBucketLocation","s3:GetBucketAcl"],
  "Resource":"arn:aws:s3:::$BUILD_BUCKET"},
 {"Sid":"EcrLogin","Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
 {"Sid":"PushImage","Effect":"Allow","Action":["ecr:BatchCheckLayerAvailability","ecr:InitiateLayerUpload",
  "ecr:UploadLayerPart","ecr:CompleteLayerUpload","ecr:PutImage"],
  "Resource":"arn:aws:ecr:$AWS_REGION:$ACCOUNT_ID:repository/$SERVICE"}]}
JSON
aws iam put-role-policy --role-name "$BUILD_ROLE" --policy-name ticketapp-image-build \
  --policy-document "file://$TMP_DIR/codebuild-policy.json" >/dev/null
info "logs, read s3://$BUILD_BUCKET/source/*, push to $SERVICE only"

# ---------------------------------------------------------------------------
step "CodeBuild project $BUILD_PROJECT (x86_64, BUILD_GENERAL1_SMALL): ~\$0.005/build minute"
EXISTING_LOG_GROUP=$(awst logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" \
  --query "logGroups[?logGroupName=='$LOG_GROUP'].logGroupName | [0]")
if [ -z "$EXISTING_LOG_GROUP" ]; then
  aws logs create-log-group --log-group-name "$LOG_GROUP" --tags project="$NAME" >/dev/null
  info "created log group $LOG_GROUP"
fi
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 14 >/dev/null
info "logs: $LOG_GROUP (14 days)"

PROJECT=$(awst codebuild batch-get-projects --names "$BUILD_PROJECT" --query 'projects[0].name')
if [ -n "$PROJECT" ]; then
  info "exists"
else
  export BUILD_BUCKET BUILD_IMAGE REGISTRY REPO_URI LOG_GROUP
  # The buildspec is generated as JSON-quoted YAML strings, so no shell or
  # YAML quoting can go wrong. Base images come from the ECR Public mirror
  # of Docker Hub's official images (same images; avoids Docker Hub's
  # anonymous pull limit on CodeBuild's shared IPs). Only the build
  # container's copy of the Dockerfile is changed, never the repository.
  python3 - "$TMP_DIR" <<'PY'
import json, os, sys
e, out = os.environ, sys.argv[1]
commands = [
    'aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$REGISTRY"',
    "sed -i 's|^FROM python:|FROM public.ecr.aws/docker/library/python:|' Dockerfile",
    'docker build -t "$REPO_URI:$IMAGE_TAG" .',
    'docker push "$REPO_URI:$IMAGE_TAG"',
]
buildspec = "version: 0.2\nphases:\n  build:\n    commands:\n" + "".join(
    f"      - {json.dumps(c)}\n" for c in commands)
json.dump({"type": "S3", "location": f"{e['BUILD_BUCKET']}/source/latest.zip", "buildspec": buildspec},
          open(f"{out}/source.json", "w"))
json.dump({"type": "LINUX_CONTAINER", "image": e["BUILD_IMAGE"], "computeType": "BUILD_GENERAL1_SMALL",
           "privilegedMode": True, "imagePullCredentialsType": "CODEBUILD",
           "environmentVariables": [
               {"name": "REGISTRY", "value": e["REGISTRY"], "type": "PLAINTEXT"},
               {"name": "REPO_URI", "value": e["REPO_URI"], "type": "PLAINTEXT"},
               {"name": "IMAGE_TAG", "value": "set-per-build", "type": "PLAINTEXT"}]},
          open(f"{out}/environment.json", "w"))
json.dump({"cloudWatchLogs": {"status": "ENABLED", "groupName": e["LOG_GROUP"]},
           "s3Logs": {"status": "DISABLED"}}, open(f"{out}/logs.json", "w"))
PY
  # A brand-new role can take a few seconds before CodeBuild may assume
  # it; retry only that error.
  [ "$ROLE_CREATED" = 0 ] || [ "$POLL_SECONDS" = 0 ] || { info "waiting 15 s for the new role"; sleep 15; }
  for attempt in 1 2 3 4; do
    if aws codebuild create-project --name "$BUILD_PROJECT" \
        --description "ticketapp backend image: docker build + push to ECR" \
        --source "file://$TMP_DIR/source.json" --artifacts type=NO_ARTIFACTS \
        --environment "file://$TMP_DIR/environment.json" \
        --service-role "arn:aws:iam::$ACCOUNT_ID:role/$BUILD_ROLE" \
        --timeout-in-minutes 30 --queued-timeout-in-minutes 30 \
        --logs-config "file://$TMP_DIR/logs.json" \
        --tags key=project,value="$NAME" >/dev/null 2>"$TMP_DIR/create-project.err"; then
      info "created"
      break
    fi
    if [ "$attempt" -lt 4 ] && grep -q 'AssumeRole' "$TMP_DIR/create-project.err"; then
      info "the role isn't usable yet; retrying in 10 s"
      sleep $((POLL_SECONDS > 0 ? 10 : 0))
    else
      cat "$TMP_DIR/create-project.err" >&2
      die "creating the CodeBuild project failed"
    fi
  done
fi

# ---------------------------------------------------------------------------
step "Building $TAG in CodeBuild (~3-5 min)"
KEY=source/$TAG.zip
git -C "$ROOT" archive --format=zip -o "$TMP_DIR/source.zip" HEAD
aws s3api put-object --bucket "$BUILD_BUCKET" --key "$KEY" --body "$TMP_DIR/source.zip" \
  --expected-bucket-owner "$ACCOUNT_ID" >/dev/null
info "uploaded git archive HEAD ($(($(wc -c < "$TMP_DIR/source.zip") / 1024)) KB, committed files only)"
BUILD_ID=$(awst codebuild start-build --project-name "$BUILD_PROJECT" \
  --source-type-override S3 --source-location-override "$BUILD_BUCKET/$KEY" \
  --environment-variables-override "name=IMAGE_TAG,value=$TAG,type=PLAINTEXT" \
  --query build.id)
[ -n "$BUILD_ID" ] || die "CodeBuild didn't return a build id"
info "build $BUILD_ID"

# The last 30 lines of the build log, with anything token-like (40+
# characters of mixed-case base64, AWS access key ids) redacted. The
# build has no secrets to print; this is a second line of defence.
show_build_log() {
  awst codebuild batch-get-builds --ids "$BUILD_ID" \
    --query "builds[0].phases[?phaseStatus && phaseStatus!='SUCCEEDED'].[phaseType, phaseStatus]" \
    | tr '\t' ' ' | sed '/^$/d; s/^/    failed phase: /' >&2 || true
  local group="" stream=""
  read -r group stream < <(awst codebuild batch-get-builds --ids "$BUILD_ID" \
    --query 'builds[0].logs.[groupName, streamName]' 2>/dev/null || true) || true
  if [ -z "$group" ] || [ -z "$stream" ] || [ "$stream" = None ]; then
    warn "no build log yet; see the build in the CodeBuild console"
    return 0
  fi
  aws logs get-log-events --log-group-name "$group" --log-stream-name "$stream" \
    --limit 30 --no-start-from-head --output json > "$TMP_DIR/build-log.json" || return 0
  printf '\n    Last 30 lines of %s:\n' "$group/$stream" >&2
  python3 - "$TMP_DIR/build-log.json" >&2 <<'PY'
import json, re, sys
token = re.compile(r"[A-Za-z0-9+/=_-]{40,}")
key_id = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
def redact(m):
    s = m.group(0)
    mixed = re.search(r"[a-z]", s) and re.search(r"[A-Z]", s) and re.search(r"[0-9]", s)
    return "[redacted]" if mixed else s
for event in json.load(open(sys.argv[1])).get("events", [])[-30:]:
    line = key_id.sub("[redacted]", token.sub(redact, event.get("message", "").rstrip("\n")))
    print("    | " + line)
PY
}

WAITED=0
LAST_PHASE=""
while :; do
  BUILD_STATUS="" PHASE=""
  read -r BUILD_STATUS PHASE < <(awst codebuild batch-get-builds --ids "$BUILD_ID" \
    --query 'builds[0].[buildStatus, currentPhase]' || true) || true
  if [ -n "$PHASE" ] && [ "$PHASE" != None ] && [ "$PHASE" != "$LAST_PHASE" ]; then info "phase: $PHASE"; LAST_PHASE=$PHASE; fi
  case "$BUILD_STATUS" in
    SUCCEEDED) info "build: SUCCEEDED"; break ;;
    FAILED|FAULT|STOPPED|TIMED_OUT)
      show_build_log
      die "build $BUILD_STATUS (image $TAG was not pushed; fix it, commit, and re-run)" ;;
  esac
  [ "$WAITED" -lt 2400 ] || die "build still '${BUILD_STATUS:-unknown}' after 2400s; see the CodeBuild console"
  sleep "$POLL_SECONDS"; WAITED=$((WAITED + POLL_SECONDS))
  [ "$POLL_SECONDS" -gt 0 ] || WAITED=$((WAITED + 1))
done
has_image "$TAG" || die "the build succeeded but $REPO_URI:$TAG isn't in ECR"

# ---------------------------------------------------------------------------
step "Image ready"
info "image: $REPO_URI:$TAG (linux/amd64)"
info "Next: run scripts/aws_up.sh"
