# Shared settings and helpers for scripts/aws_up.sh and scripts/aws_down.sh.
# Sourced, not run. Nothing here prints a secret: secret values are only
# ever written to a private temp file or passed to AWS by ARN.

set -euo pipefail
set +x  # never trace: a traced command line could show a value

export AWS_PROFILE="${AWS_PROFILE:-ticketapp}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""

NAME=ticketapp
SERVICE=ticketapp-api          # ECS Express service (and ECR repository)
DB_ID=ticketapp-db             # RDS instance, subnet group, snapshot prefix
API_SG_NAME=ticketapp-api
DB_SG_NAME=ticketapp-db
APP_SECRET_NAME=ticketapp/app
AMPLIFY_NAME=ticketapp-web
EXEC_ROLE=ticketapp-ecs-exec
INFRA_ROLE=ticketapp-ecs-infra
TASK_ROLE=ticketapp-api-task
DB_NAME=ticketapp
DB_USER=ticketapp_admin
BEDROCK_MODEL=apac.amazon.nova-lite-v1:0
KEEP_SNAPSHOTS="${KEEP_SNAPSHOTS:-2}"
# Poll interval for the waits below; tests set it to 0.
POLL_SECONDS="${POLL_SECONDS:-15}"

STEP=0
step() { STEP=$((STEP + 1)); printf '\n[%d] %s\n' "$STEP" "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    WARNING: %s\n' "$*" >&2; }
die() { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }
# Private scratch dir (policy documents, the generated secret, the web zip),
# removed on exit however the script ends.
TMP_DIR=$(mktemp -d)
chmod 700 "$TMP_DIR"
on_exit() {
  local rc=$?
  rm -rf "$TMP_DIR"
  [ $rc -eq 0 ] || printf '\nFailed at step %d (exit %d). Nothing after it ran; re-running the script is safe.\n' "$STEP" "$rc" >&2
}
trap on_exit EXIT

# `aws` with text output; "None" (an empty query result) becomes "".
# Ends with a newline so `read` gets a whole line.
awst() {
  local out
  out=$(aws "$@" --output text)
  [ "$out" != "None" ] || out=""
  printf '%s\n' "$out"
}

need() { command -v "$1" >/dev/null 2>&1 || die "$1 is not installed"; }

preflight() {
  need aws
  need python3
  ACCOUNT_ID=$(awst sts get-caller-identity --query Account) \
    || die "AWS profile '$AWS_PROFILE' can't authenticate (aws sso login / aws configure)"
  info "profile $AWS_PROFILE, region $AWS_REGION"
}

# --- lookups (all return "" when the thing doesn't exist) -----------------
service_arn() {
  awst ecs describe-services --cluster default --services "$SERVICE" \
    --query "services[?status=='ACTIVE'].serviceArn | [0]" 2>/dev/null || true
}

db_status() {
  awst rds describe-db-instances --db-instance-identifier "$DB_ID" \
    --query 'DBInstances[0].DBInstanceStatus' 2>/dev/null || true
}

# Manual snapshots of this app, oldest first, one per line.
db_snapshots() {
  awst rds describe-db-snapshots --snapshot-type manual \
    --query "sort_by(DBSnapshots[?starts_with(DBSnapshotIdentifier, '$DB_ID-') && Status=='available'], &SnapshotCreateTime)[].DBSnapshotIdentifier" \
    | tr '\t' '\n' | sed '/^$/d'
}

# Polls until `$@` prints one of the wanted values (space-separated in
# $WANT) or a failure value ($FAIL), for at most $1 seconds.
wait_for() {
  local limit=$1 label=$2; shift 2
  local waited=0 value
  while :; do
    value=$("$@" || true)
    for w in $WANT; do [ "$value" = "$w" ] && { info "$label: $value"; return 0; }; done
    for f in ${FAIL:-}; do [ "$value" = "$f" ] && die "$label: $value"; done
    [ "$waited" -ge "$limit" ] && die "$label: still '${value:-unknown}' after ${limit}s"
    sleep "$POLL_SECONDS"; waited=$((waited + POLL_SECONDS))
    [ "$POLL_SECONDS" -gt 0 ] || waited=$((waited + 1))
  done
}
