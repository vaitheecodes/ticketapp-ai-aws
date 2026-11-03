#!/usr/bin/env bash
# Take the demo stack down after a demo (docs/AWS_DEPLOY.md, "Demo-only
# hosting"). Asks you to type "yes", then:
#   1. snapshots the database and waits until the snapshot is available
#      (before anything is deleted; if it fails, nothing is deleted);
#   2. deletes the ECS Express service (task + load balancer);
#   3. deletes the RDS instance (no second snapshot: step 1 is it);
#   4. deletes manual snapshots older than the newest KEEP_SNAPSHOTS
#      (default 2), only after the new one is available;
#   5. lists anything still billing by the hour.
# Kept (cents a month): the snapshots, the app secret, the ECR images, the
# Amplify site, IAM roles, security groups, logs.
#
# Idempotent: re-run it after a failure; steps already done are skipped.
# Stop using the app before running it: writes after step 1 starts are
# not in the snapshot.

source "$(dirname "$0")/aws_lib.sh"

step "Checking the AWS identity"
preflight

SERVICE_ARN=$(service_arn)
STATUS=$(db_status)
if [ -n "$SERVICE_ARN" ]; then info "API service:  running"; else info "API service:  none"; fi
info "database:     ${STATUS:-none}"
info "snapshots:    $(db_snapshots | grep -c . || true) now (the newest $KEEP_SNAPSHOTS are kept)"

if [ -z "$SERVICE_ARN" ] && [ -z "$STATUS" ]; then
  info "Nothing to take down."
else
  case "$STATUS" in
    ""|available|stopped) ;;
    *) die "the database is '$STATUS'; wait until it is available (or gone) and re-run" ;;
  esac
  if [ -n "$STATUS" ]; then
    printf '\nThis will snapshot the database, then delete the API service and the database.\n'
  else
    printf '\nThere is no database (nothing to snapshot). This will delete the API service.\n'
  fi
  printf 'Older snapshots beyond the newest %s are deleted permanently.\n' "$KEEP_SNAPSHOTS"
  read -r -p 'Type "yes" to continue: ' ANSWER || ANSWER=""
  [ "$ANSWER" = "yes" ] || die "not confirmed; nothing was changed"

  # -------------------------------------------------------------------------
  if [ -n "$STATUS" ]; then
    SNAPSHOT=$DB_ID-$(date -u +%Y%m%d-%H%M%S)
    step "Final snapshot $SNAPSHOT (before deleting anything)"
    aws rds create-db-snapshot --db-instance-identifier "$DB_ID" --db-snapshot-identifier "$SNAPSHOT" \
      --tags Key=project,Value="$NAME" >/dev/null
    snapshot_status() { awst rds describe-db-snapshots --db-snapshot-identifier "$SNAPSHOT" \
      --query 'DBSnapshots[0].Status'; }
    WANT=available FAIL="failed incompatible-restore" wait_for 3600 "snapshot" snapshot_status
  else
    step "No database: no snapshot needed"
  fi

  # -------------------------------------------------------------------------
  step "Deleting the API service (Fargate task + load balancer)"
  if [ -n "$SERVICE_ARN" ]; then
    aws ecs delete-express-gateway-service --service-arn "$SERVICE_ARN" >/dev/null
    aws ecs wait services-inactive --cluster default --services "$SERVICE"
    info "deleted"
  else
    info "already gone"
  fi

  # -------------------------------------------------------------------------
  step "Deleting the database"
  if [ -n "$STATUS" ]; then
    aws rds modify-db-instance --db-instance-identifier "$DB_ID" --no-deletion-protection \
      --apply-immediately >/dev/null
    aws rds delete-db-instance --db-instance-identifier "$DB_ID" \
      --skip-final-snapshot --delete-automated-backups >/dev/null
    aws rds wait db-instance-deleted --db-instance-identifier "$DB_ID"
    info "deleted (its RDS-managed password secret goes with it)"
  else
    info "already gone"
  fi
fi

# ---------------------------------------------------------------------------
step "Snapshots: keeping the newest $KEEP_SNAPSHOTS"
SNAPSHOTS=()
while IFS= read -r s; do SNAPSHOTS+=("$s"); done < <(db_snapshots)
EXTRA=$(( ${#SNAPSHOTS[@]} - KEEP_SNAPSHOTS ))
if [ "$KEEP_SNAPSHOTS" -lt 1 ]; then
  warn "KEEP_SNAPSHOTS must be at least 1; deleting none"
elif [ "$EXTRA" -gt 0 ]; then
  for old in "${SNAPSHOTS[@]:0:$EXTRA}"; do
    aws rds delete-db-snapshot --db-snapshot-identifier "$old" >/dev/null
    info "deleted $old"
  done
fi
info "kept: $(db_snapshots | tr '\n' ' ')"

# ---------------------------------------------------------------------------
step "Anything still billing by the hour?"
LEFT=0
[ -z "$(service_arn)" ] || { warn "the API service $SERVICE still exists"; LEFT=1; }
[ -z "$(db_status)" ] || { warn "the database $DB_ID still exists"; LEFT=1; }
LBS=$(awst elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerName')
if [ -n "$LBS" ]; then
  warn "load balancers in $AWS_REGION: $LBS (~\$28/month each with their IPs; Express may take a few minutes to remove its own)"
  LEFT=1
fi
IDLE_IPS=$(awst ec2 describe-addresses --query 'Addresses[?AssociationId==null].PublicIp')
[ -z "$IDLE_IPS" ] || { warn "unattached Elastic IPs: $IDLE_IPS (\$3.65/month each)"; LEFT=1; }
if [ "$LEFT" = 0 ]; then
  info "No. What remains costs about \$1-4 a month (snapshots, app secret, ECR, Amplify)."
  info "Next demo: scripts/aws_up.sh"
fi
