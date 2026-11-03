# Deploying to AWS (stage A4 plan)

**Status: plan only. Nothing here has been run; no AWS resource exists
for it yet.** Region `ap-south-1` (Mumbai), CLI profile `ticketapp`.

Commands marked **💲** create something that bills (most of it by the
hour, whether or not anyone uses the app). Everything else is free (IAM,
security groups, configuration). Prices are on-demand ap-south-1 rates
from AWS's public Price List files (downloaded 3 Nov 2026; publication
dates: RDS 2026-10-29, ECS/Fargate 2026-10-19, VPC 2026-11-03, App Runner
2026-09-11).

## 1. Hosting choice: ECS Fargate (Express Mode), not App Runner

**App Runner is closed to new customers** (since 30 April 2026, per AWS's
"App Runner availability change" page; existing customers keep it, no new
features). AWS points new users to **ECS Express Mode**: one call takes an
image and two IAM roles and creates a Fargate service, an internet-facing
ALB with an HTTPS URL (`<name>.ecs.ap-south-1.on.aws`, ACM certificate
included), security groups, auto scaling and a log group. Express Mode
itself is free; you pay for Fargate, the ALB, logs and data transfer.

| | App Runner | ECS Fargate (Express Mode) |
|---|---|---|
| Can this account use it | Only if it was already an App Runner customer | Yes |
| Work after the response (`BackgroundTasks`: incident correlation, AI hand-off briefs, emails) | **Risky.** Instances between requests are "provisioned": billed for memory only, and CPU isn't guaranteed outside a request, so work after the response can stall | Fine: a normal long-running container with its full CPU all the time |
| Reaching private RDS | VPC connector; then **all** outbound traffic goes through the VPC, so Bedrock/S3/Secrets Manager need a NAT gateway (~$41/month) or interface endpoints | Tasks in the default VPC's public subnets with a public IP: no NAT; RDS stays private, reachable only from the task's security group |
| Long AI calls | Fixed request timeout (120 s, as far as I know; not re-checked) | ALB idle timeout, default 60 s, can be raised (plan: 120 s) |
| Large uploads (5 × 10 MB per message) | No issue | No issue (no ALB body limit; Starlette spools large bodies to /tmp) |
| 10 s pool wait (`DB_POOL_TIMEOUT`) | Same on both: a request can wait up to 10 s for a connection, then 503 | Same |
| Idle cost | Low (memory only) **plus NAT** | Task + ALB + public IPv4 bill all the time |

**Recommendation: ECS Fargate via Express Mode**, 1 task (0.5 vCPU / 1 GB,
ARM64/Graviton), default VPC public subnets, private RDS.

### What differs from a long-running server (and what's done about it)

- **Deploys and scale-in stop the task.** ECS sends SIGTERM and kills the
  task 30 s later (Express default `stopTimeout`). uvicorn runs with
  `--timeout-graceful-shutdown 25`, so in-flight requests and their
  background tasks get 25 s. Work queued after that is lost (it's
  best-effort today too: incident correlation reruns on the next ticket or
  "Check now").
- **Canary deployments run old and new tasks together** for a few
  minutes. Startup migrations take an advisory lock (`db_postgres.migrate`),
  so that's safe.
- **No local disk that survives.** The container disk is ephemeral: the
  app must run with `DB_BACKEND=postgres` and `STORAGE_BACKEND=s3`. SQLite
  or local uploads on Fargate would be wiped on every deploy.
- **Per-process state.** The login rate limiter's counters and the DB pool
  are per task. The plan pins the service to 1 task (`maxTaskCount 1`).
  With more tasks, the per-IP/account limits apply per task (multiply by
  the task count), and `DB_POOL_MAX` × tasks must stay below RDS
  `max_connections` (db.t4g.micro: about 80 usable).
- **The client IP comes from the ALB.** The socket peer is the ALB, so
  `TRUSTED_PROXY_HOPS=1` makes the rate limiter read the last
  `X-Forwarded-For` entry (the one the ALB appended).
- **Keep-alive vs the ALB.** uvicorn's keep-alive (130 s) is longer than
  the ALB idle timeout (120 s), so the ALB never reuses a connection
  uvicorn just closed (that shows up as random 502s).
- **Long AI turns.** A chat turn can make several model calls.
  `AI_TIMEOUT_SECONDS` defaults to 90 s per call; on Bedrock set it to
  30 s so one slow call can't hold a request past the ALB timeout. Nova
  Lite calls took about 1 s each in the A1 tests.
- **Password rotation.** RDS rotates the Secrets-Manager-managed master
  password on a schedule (7 days by default, per secondary sources;
  confirm in the RDS guide). ECS injects secrets only when a task
  starts, so after a rotation new DB connections fail until the task is
  restarted. Either restart after each rotation (`aws ecs update-service
  --force-new-deployment`, step 9) or lengthen the schedule (step 3).

### Monthly estimate: low-traffic demo, running 24/7 (730 h)

| Item | Basis | $/month |
|---|---|---|
| Fargate task, ARM64 0.5 vCPU / 1 GB | 0.5 × $0.02383 + 1 × $0.00261 per h | 10.60 |
| (same on x86_64 instead) | 0.5 × $0.04256 + 1 × $0.004655 per h | (18.93) |
| Public IPv4 on the task | $0.005/h | 3.65 |
| ALB hours | $0.0239/h | 17.45 |
| ALB LCUs | $0.008/LCU-h; a demo uses a fraction of one | ~1 |
| ALB public IPv4, one per AZ (2-3) | $0.005/h each | 7.30-10.95 |
| RDS PostgreSQL db.t4g.micro, Single-AZ | $0.021/h | 15.33 |
| RDS storage, 20 GB gp3 | $0.131/GB-month; backups up to the DB size are free | 2.62 |
| Secrets Manager, 2 secrets (RDS-managed + app) | $0.40 each | 0.80 |
| CloudWatch Logs + 1 alarm | $0.67/GB ingested, $0.03/GB stored | ~0.40 |
| ECR, about 0.5 GB of images | $0.10/GB-month | 0.05 |
| Amplify hosting (manual deploys, no builds) | $0.023/GB stored, $0.15/GB served | ~0.20 |
| Bedrock Nova Lite, a few thousand calls | $0.071 / $0.284 per 1M tokens in/out | ~0.20 |
| S3 attachments | cents | ~0.05 |
| NAT gateway | **not used** ($0.056/h + $0.056/GB = ~$41 if it were) | 0 |
| **Total (ARM64)** | | **~$60-63** |

- **Billed while idle:** everything above except Bedrock, LCUs and
  transfer, about $59-62 of the total. Biggest items: the ALB with its
  IPs (~$25-28), RDS (~$18), the task with its IP (~$14).
- **To pause:**
  - Scale the service to 0 (`--scaling-target minTaskCount=0`) to save
    about $14. The ALB keeps billing.
  - Stop RDS (`aws rds stop-db-instance`) to save the instance hours, not
    the storage. AWS starts it again by itself after 7 days.
  - Deleting everything (section 10) is the only way to reach $0.
- **Free tier:**
  - Accounts created since AWS's July 2025 change get $100 in credits
    (up to $200 with activities) for 6 months, on a free or paid plan.
    The free plan limits which services can be used, so this deploy will
    probably need the paid plan, with credits applied.
  - At ~$60/month, the credits cover about 3 months.
  - Older accounts may still have the 12-month RDS micro allowance.
  - I couldn't check which applies (`freetier:GetAccountPlanState` is
    denied for `ticketapp-dev`). Check Billing → Free Tier in the console.
- **App Runner, for comparison** (only if the account were eligible), with
  RDS kept private:
  - provisioned memory ~$5.40 plus active time ~$1.40 (40 h at 0.5 vCPU)
  - NAT gateway ~$41 plus its IPv4 address $3.65
  - RDS ~$18, secrets and logs ~$1
  - **total ≈ $70**, with the background-task risk above.

## 2. What the deploying identity needs

`ticketapp-dev` can today only use Bedrock and the attachments bucket.
Run this plan with an administrator profile, or add these to the
deployer (not to the app's role):

- ECR (repository, push)
- ECS (Express services)
- IAM: create roles and `iam:PassRole` for the three roles below
- RDS
- EC2: security groups, describe VPC/subnets
- Secrets Manager
- CloudWatch Logs
- Elastic Load Balancing (modify attributes)
- Amplify

Every command below uses `--profile ticketapp --region ap-south-1` via:

```bash
export AWS_PROFILE=ticketapp AWS_REGION=ap-south-1 AWS_PAGER=""
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET=ticketapp-ai-attachments-$ACCOUNT_ID
```

Check that the installed CLI knows Express Mode's architecture option
(older CLIs don't; without it the service runs x86_64, which also works
because the image is built for both):

```bash
aws ecs create-express-gateway-service help | grep -c -- --cpu-architecture
```

## 3. Database: RDS PostgreSQL (private, SSL, password in Secrets Manager)

RDS PostgreSQL 18 matches what A2 was tested on (18.3 locally). With
`rds.force_ssl` defaulting to 1 on PostgreSQL 15 and later, the server
refuses non-SSL connections, and the app also requires SSL
(`DB_SSLMODE=require`; `auth.check_production_config` refuses anything
weaker in production).

```bash
VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
SUBNETS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC_ID Name=default-for-az,Values=true \
  --query 'Subnets[].SubnetId' --output text)

# Security groups: one for the API tasks, one for the database. The DB
# group allows 5432 only from the API group (no CIDR, no public access).
API_SG=$(aws ec2 create-security-group --group-name ticketapp-api --vpc-id $VPC_ID \
  --description "ticketapp API tasks" --query GroupId --output text)
DB_SG=$(aws ec2 create-security-group --group-name ticketapp-db --vpc-id $VPC_ID \
  --description "ticketapp RDS, from API tasks only" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $DB_SG --protocol tcp --port 5432 --source-group $API_SG

aws rds create-db-subnet-group --db-subnet-group-name ticketapp-db \
  --db-subnet-group-description "ticketapp default VPC" --subnet-ids $SUBNETS

# Pick the newest 18.x offered for db.t4g.micro:
aws rds describe-orderable-db-instance-options --engine postgres --db-instance-class db.t4g.micro \
  --query "OrderableDBInstanceOptions[?starts_with(EngineVersion,'18')].EngineVersion" --output text
PG_VERSION=18.x   # set from the line above
```

**💲 RDS instance** (~$18/month while it exists, running or not; storage
always bills):

```bash
aws rds create-db-instance \
  --db-instance-identifier ticketapp-db \
  --engine postgres --engine-version $PG_VERSION \
  --db-instance-class db.t4g.micro \
  --allocated-storage 20 --storage-type gp3 --storage-encrypted \
  --db-name ticketapp \
  --master-username ticketapp_admin \
  --manage-master-user-password \
  --vpc-security-group-ids $DB_SG --db-subnet-group-name ticketapp-db \
  --no-publicly-accessible --no-multi-az \
  --backup-retention-period 1 \
  --deletion-protection \
  --auto-minor-version-upgrade \
  --tags Key=project,Value=ticketapp
aws rds wait db-instance-available --db-instance-identifier ticketapp-db

DB_HOST=$(aws rds describe-db-instances --db-instance-identifier ticketapp-db \
  --query 'DBInstances[0].Endpoint.Address' --output text)
DB_SECRET_ARN=$(aws rds describe-db-instances --db-instance-identifier ticketapp-db \
  --query 'DBInstances[0].MasterUserSecret.SecretArn' --output text)
```

- `--manage-master-user-password` means RDS generates the password and
  stores it in Secrets Manager. It never appears in a command, file or
  terminal. The secret is JSON with `username` and `password`.
- Optional, to avoid restarts after rotation:
  `aws secretsmanager rotate-secret --secret-id $DB_SECRET_ARN --rotation-rules ScheduleExpression="rate(90 days)"`
  (managed rotation; check that RDS accepts the schedule).
- **Known gap:** the app connects as the master user. A separate
  least-privilege role would need a SQL session inside the VPC (no
  bastion in this plan); do it later with a one-off ECS task.

## 4. App secrets (Secrets Manager)

**💲 one secret** ($0.40/month). JWT_SECRET and ADMIN_PASSWORD are
generated into a private temp file, never printed, and the file is
removed:

```bash
umask 077; SECRET_FILE=$(mktemp)
python3 - "$SECRET_FILE" <<'PY'
import json, secrets, sys
json.dump({"JWT_SECRET": secrets.token_hex(32),
           "ADMIN_PASSWORD": secrets.token_urlsafe(18)}, open(sys.argv[1], "w"))
PY
APP_SECRET_ARN=$(aws secretsmanager create-secret --name ticketapp/app \
  --description "ticketapp API: JWT signing key, first admin password" \
  --secret-string "file://$SECRET_FILE" --query ARN --output text)
rm -f "$SECRET_FILE"
```

To sign in as the admin the first time, copy the password to the
clipboard without showing it, then change it in the app:

```bash
aws secretsmanager get-secret-value --secret-id ticketapp/app --query SecretString --output text \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["ADMIN_PASSWORD"], end="")' | pbcopy
```

## 5. Image: ECR

**💲 storage only** (~$0.10/GB-month):

```bash
aws ecr create-repository --repository-name ticketapp-api \
  --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE
aws ecr put-lifecycle-policy --repository-name ticketapp-api --lifecycle-policy-text \
  '{"rules":[{"rulePriority":1,"description":"keep last 5","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":5},"action":{"type":"expire"}}]}'

REGISTRY=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
TAG=$(git rev-parse --short HEAD)
aws ecr get-login-password | docker login --username AWS --password-stdin $REGISTRY
# Both architectures, so the service can run ARM64 or x86_64:
docker buildx build --platform linux/arm64,linux/amd64 -t $REGISTRY/ticketapp-api:$TAG --push .
IMAGE=$REGISTRY/ticketapp-api:$TAG
```

The image (`Dockerfile`):
- based on `python:3.13-slim`, with binary wheels from
  `requirements-runtime.txt` only (no pandas, pyarrow, pytest or openai);
- runs as the non-root user `app` (uid 10001), and the code isn't
  writable;
- has a `HEALTHCHECK` on `/health`;
- sets `APP_ENV=production` by default, so it refuses to start without
  strong secrets.

`.dockerignore` is an allow-list. `tests/test_dockerfile.py` checks that
every file the backend reads at runtime is copied and that `.env`, local
DBs and uploads are not.

## 6. IAM roles

Trust policies (free):

```bash
cat > /tmp/ecs-tasks-trust.json <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},
 "Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"$ACCOUNT_ID"}}}]}
JSON
cat > /tmp/ecs-infra-trust.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs.amazonaws.com"},
 "Action":"sts:AssumeRole"}]}
JSON
```

**Task execution role**: used by ECS (not the app) to pull the image,
write logs and read the two secrets at task start:

```bash
aws iam create-role --role-name ticketapp-ecs-exec --assume-role-policy-document file:///tmp/ecs-tasks-trust.json
aws iam attach-role-policy --role-name ticketapp-ecs-exec \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam put-role-policy --role-name ticketapp-ecs-exec --policy-name read-ticketapp-secrets --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",
  \"Resource\":[\"$APP_SECRET_ARN\",\"$DB_SECRET_ARN\"]}]}"
```

(The RDS-managed secret is encrypted with the default
`aws/secretsmanager` key, which needs no extra `kms:Decrypt` grant.)

**Infrastructure role**: Express Mode creates the ALB, target groups and
security groups with it:

```bash
aws iam create-role --role-name ticketapp-ecs-infra --assume-role-policy-document file:///tmp/ecs-infra-trust.json
aws iam attach-role-policy --role-name ticketapp-ecs-infra \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices
```

**Task role**: what the app's code may do, and nothing more.
- Bedrock: invoke Nova Lite through the APAC inference profile. A
  cross-region profile routes to the model in several APAC regions,
  hence `*` in the foundation-model ARN's region.
- S3: put, get and delete under `attachments/` only (the calls
  `backend/storage.py` makes). No list, no bucket-level actions.

```bash
aws iam create-role --role-name ticketapp-api-task --assume-role-policy-document file:///tmp/ecs-tasks-trust.json
aws iam put-role-policy --role-name ticketapp-api-task --policy-name ticketapp-api --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
  {\"Sid\":\"Bedrock\",\"Effect\":\"Allow\",\"Action\":[\"bedrock:InvokeModel\"],
   \"Resource\":[\"arn:aws:bedrock:$AWS_REGION:$ACCOUNT_ID:inference-profile/apac.amazon.nova-lite-v1:0\",
                 \"arn:aws:bedrock:*::foundation-model/amazon.nova-lite-v1:0\"]},
  {\"Sid\":\"Attachments\",\"Effect\":\"Allow\",\"Action\":[\"s3:PutObject\",\"s3:GetObject\",\"s3:DeleteObject\"],
   \"Resource\":\"arn:aws:s3:::$BUCKET/attachments/*\"}]}"
```

If `BEDROCK_MODEL_ID_CHAT` is set to another model (e.g. Nova Pro), add
its profile and foundation-model ARNs too. The Converse API is authorised
by `bedrock:InvokeModel`.

## 7. Frontend app on Amplify (created first: its URL is the CORS origin)

Creating the app is free; hosting bills only for storage and transfer.

```bash
AMPLIFY_APP_ID=$(aws amplify create-app --name ticketapp-web --platform WEB \
  --custom-rules '[{"source":"</^[^.]+$|\\.(?!(css|gif|ico|jpg|js|png|txt|svg|woff|woff2|ttf|map|json|webp)$)([^.]+$)/>","target":"/index.html","status":"200"}]' \
  --query app.appId --output text)
aws amplify create-branch --app-id $AMPLIFY_APP_ID --branch-name main
FRONTEND_ORIGIN=https://main.$AMPLIFY_APP_ID.amplifyapp.com
```

(The rewrite rule serves `index.html` for the React Router paths, such as
`/customer` and `/admin`, so a reload doesn't 404.)

## 8. Backend service (ECS Express Mode)

Environment (plain values) and secrets (injected from Secrets Manager at
task start, never in the task definition as text):

| Variable | Value | Why |
|---|---|---|
| `APP_ENV` | `production` | strict startup checks, no demo staff |
| `AI_PROVIDER` / `AWS_REGION` | `bedrock` / `ap-south-1` | |
| `BEDROCK_MODEL_ID` | `apac.amazon.nova-lite-v1:0` | |
| `AI_TIMEOUT_SECONDS` | `30` | stays under the ALB timeout |
| `STORAGE_BACKEND` / `S3_BUCKET` / `S3_PREFIX` | `s3` / `$BUCKET` / `attachments/` | |
| `DB_BACKEND` / `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` | `postgres` / `$DB_HOST` / `5432` / `ticketapp` / `ticketapp_admin` | |
| `DB_SSLMODE` | `require` | production refuses less |
| `DB_POOL_MAX` / `DB_POOL_TIMEOUT` | `10` / `10` | |
| `ALLOWED_ORIGINS` | `$FRONTEND_ORIGIN` | CORS: the Amplify site only |
| `ADMIN_EMAIL` | your admin email | |
| `TRUSTED_PROXY_HOPS` | `1` | client IP behind the ALB |
| `EMAIL_ENABLED` | `false` | no SMTP in this plan |
| secret `JWT_SECRET` | `$APP_SECRET_ARN:JWT_SECRET::` | |
| secret `ADMIN_PASSWORD` | `$APP_SECRET_ARN:ADMIN_PASSWORD::` | first start only |
| secret `DB_PASSWORD` | `$DB_SECRET_ARN:password::` | RDS-managed |

Login limits keep their defaults unless set:
- 30 attempts per IP per 5 min;
- 5 failures per account per 15 min.

The variables are `LOGIN_IP_MAX_ATTEMPTS`, `LOGIN_IP_WINDOW_SECONDS`,
`LOGIN_ACCOUNT_MAX_FAILURES` and `LOGIN_ACCOUNT_WINDOW_SECONDS`, and
`LOGIN_RATE_LIMIT_ENABLED` turns the limiter off.

**💲 Express service: Fargate task + ALB + public IPv4** (~$40/month
with the ALB):

```bash
ADMIN_EMAIL=you@example.com   # set yours
SUBNET_LIST=$(echo $SUBNETS | tr ' ' ',')
aws ecs create-express-gateway-service \
  --service-name ticketapp-api \
  --execution-role-arn arn:aws:iam::$ACCOUNT_ID:role/ticketapp-ecs-exec \
  --infrastructure-role-arn arn:aws:iam::$ACCOUNT_ID:role/ticketapp-ecs-infra \
  --task-role-arn arn:aws:iam::$ACCOUNT_ID:role/ticketapp-api-task \
  --cpu 512 --memory 1024 \
  --cpu-architecture ARM64 \
  --health-check-path /health \
  --scaling-target '{"minTaskCount":1,"maxTaskCount":1}' \
  --network-configuration "{\"subnets\":[\"${SUBNET_LIST//,/\",\"}\"],\"securityGroups\":[\"$API_SG\"]}" \
  --primary-container "{
    \"image\":\"$IMAGE\",\"containerPort\":8000,
    \"environment\":[
      {\"name\":\"APP_ENV\",\"value\":\"production\"},
      {\"name\":\"AI_PROVIDER\",\"value\":\"bedrock\"},
      {\"name\":\"AWS_REGION\",\"value\":\"$AWS_REGION\"},
      {\"name\":\"BEDROCK_MODEL_ID\",\"value\":\"apac.amazon.nova-lite-v1:0\"},
      {\"name\":\"AI_TIMEOUT_SECONDS\",\"value\":\"30\"},
      {\"name\":\"STORAGE_BACKEND\",\"value\":\"s3\"},
      {\"name\":\"S3_BUCKET\",\"value\":\"$BUCKET\"},
      {\"name\":\"S3_PREFIX\",\"value\":\"attachments/\"},
      {\"name\":\"DB_BACKEND\",\"value\":\"postgres\"},
      {\"name\":\"DB_HOST\",\"value\":\"$DB_HOST\"},
      {\"name\":\"DB_PORT\",\"value\":\"5432\"},
      {\"name\":\"DB_NAME\",\"value\":\"ticketapp\"},
      {\"name\":\"DB_USER\",\"value\":\"ticketapp_admin\"},
      {\"name\":\"DB_SSLMODE\",\"value\":\"require\"},
      {\"name\":\"DB_POOL_MAX\",\"value\":\"10\"},
      {\"name\":\"DB_POOL_TIMEOUT\",\"value\":\"10\"},
      {\"name\":\"ALLOWED_ORIGINS\",\"value\":\"$FRONTEND_ORIGIN\"},
      {\"name\":\"ADMIN_EMAIL\",\"value\":\"$ADMIN_EMAIL\"},
      {\"name\":\"TRUSTED_PROXY_HOPS\",\"value\":\"1\"},
      {\"name\":\"EMAIL_ENABLED\",\"value\":\"false\"}],
    \"secrets\":[
      {\"name\":\"JWT_SECRET\",\"valueFrom\":\"$APP_SECRET_ARN:JWT_SECRET::\"},
      {\"name\":\"ADMIN_PASSWORD\",\"valueFrom\":\"$APP_SECRET_ARN:ADMIN_PASSWORD::\"},
      {\"name\":\"DB_PASSWORD\",\"valueFrom\":\"$DB_SECRET_ARN:password::\"}]}" \
  --monitor-resources
```

- Drop `--cpu-architecture ARM64` if the CLI check in section 2 printed
  0. The task then runs x86_64 (+~$8/month).
- If Express rejects 512/1024, use its default of 1024/2048 (about +$11
  ARM64, or +$19 x86_64).

**Security groups.**
- Express Mode documents a security group passed in
  `--network-configuration` as "an additional ingress path". It should
  still create its own service and load-balancer groups, so `ticketapp-api`
  only adds DB access.
- Check that the tasks carry both groups, `ticketapp-api` and the
  Express-created service group:
  ```bash
  aws ecs describe-services --cluster default --services ticketapp-api \
    --query 'services[0].networkConfiguration.awsvpcConfiguration.securityGroups'
  ```
- If only `ticketapp-api` is listed, allow the ALB in. Find the ALB's
  group in the EC2 console, then:
  `aws ec2 authorize-security-group-ingress --group-id $API_SG --protocol tcp --port 8000 --source-group <alb-sg>`
- The default VPC's public subnets give each task a public IP (outbound
  to Bedrock, S3, Secrets Manager and ECR without NAT). Inbound to the
  task is only from the ALB's group.

**After creation:**

```bash
SERVICE_URL=https://$(aws ecs describe-express-gateway-service --service-arn \
  $(aws ecs describe-services --cluster default --services ticketapp-api --query 'services[0].serviceArn' --output text) \
  --query 'service.activeConfigurations[0].ingressPaths[0].endpoint' --output text)
curl -s $SERVICE_URL/health          # {"ok":true}

# ALB idle timeout 60 s -> 120 s (long AI turns). Express shares the ALB
# across services; re-check after Express updates, it may reset it.
ALB_ARN=$(aws elbv2 describe-load-balancers --query \
  "LoadBalancers[?contains(DNSName, 'ecs-express') || contains(LoadBalancerName, 'ecs-express')].LoadBalancerArn | [0]" --output text)
aws elbv2 modify-load-balancer-attributes --load-balancer-arn $ALB_ARN \
  --attributes Key=idle_timeout.timeout_seconds,Value=120

# Logs: keep 14 days instead of forever.
LOG_GROUP=$(aws logs describe-log-groups --log-group-name-prefix /aws/ecs/default/ticketapp-api \
  --query 'logGroups[0].logGroupName' --output text)
aws logs put-retention-policy --log-group-name $LOG_GROUP --retention-in-days 14
aws logs tail $LOG_GROUP --follow     # startup: migrations, "Application startup complete"
```

The output path `activeConfigurations[].ingressPaths[].endpoint` is in
the CLI's help (aws-cli 2.36). Two things are my best guess:
- whether `endpoint` already includes `https://` (drop the prefix if so);
- the ALB name filter.

Check both on the first run. The console's service page shows the URL
and the ALB.

- **Health check:** ALB → `GET /health`. It needs no auth and touches no
  DB or AI, so a DB blip doesn't make the ALB kill a healthy task. The
  container's `HEALTHCHECK` uses the same path.
- **Migrations** run at startup (`db.init_db` → `db_postgres.migrate`,
  under an advisory lock). To apply them separately, run the image with
  `python scripts/db_migrate.py` as a one-off task.
- **Startup refusals** show as one line in the log, for example
  `STARTUP CONFIG ERROR: DB_SSLMODE must require SSL`. The line names the
  variable, never its value.

## 9. Frontend build and deploy (Amplify, manual zip, no Git connection)

`VITE_API_URL` is inlined at build time (`frontend/src/api.js`).

**💲 storage and transfer only** (cents):

```bash
(cd frontend && npm ci && VITE_API_URL=$SERVICE_URL/api npm run build && cd dist && zip -qr ../../web.zip .)
read -r JOB_ID UPLOAD_URL < <(aws amplify create-deployment --app-id $AMPLIFY_APP_ID --branch-name main \
  --query '[jobId, zipUploadUrl]' --output text)
curl -s -T web.zip "$UPLOAD_URL"
aws amplify start-deployment --app-id $AMPLIFY_APP_ID --branch-name main --job-id $JOB_ID
rm -f web.zip
echo $FRONTEND_ORIGIN
```

**CORS:**
- `ALLOWED_ORIGINS` must be exactly `$FRONTEND_ORIGIN`: scheme and host,
  no trailing slash, no wildcard. Credentials are allowed, so `*` is
  invalid anyway.
- To add a custom domain later, append it, comma-separated, then
  `aws ecs update-express-gateway-service ...` with the new
  `primaryContainer` environment.

**New backend image:** build and push a new `$TAG`, then
`aws ecs update-express-gateway-service --service-arn <arn> --primary-container '{"image":"<new image>", ...}'`.
Pass the full container block again (environment and secrets). The
deployment is a canary.

**Restart after a DB password rotation:**
`aws ecs update-service --cluster default --service ticketapp-api --force-new-deployment`.

## 10. Smoke test, then teardown

**Smoke test:**
1. Sign in as the admin and change the password.
2. Create a technician.
3. As a customer, sign up, start a chat (Bedrock reply), raise a ticket
   with an attachment (S3 round trip), and download it.
4. Six wrong passwords for one account give 429.

**Teardown** (stops all charges; the snapshot keeps the data for
$0.095/GB-month):

```bash
aws ecs delete-express-gateway-service --service-arn <arn>      # service, ALB, Express SGs, alarm
aws rds modify-db-instance --db-instance-identifier ticketapp-db --no-deletion-protection --apply-immediately
aws rds delete-db-instance --db-instance-identifier ticketapp-db --final-db-snapshot-identifier ticketapp-db-final
aws rds wait db-instance-deleted --db-instance-identifier ticketapp-db
aws rds delete-db-subnet-group --db-subnet-group-name ticketapp-db
aws ec2 delete-security-group --group-id $DB_SG && aws ec2 delete-security-group --group-id $API_SG
aws secretsmanager delete-secret --secret-id ticketapp/app --recovery-window-in-days 7
aws ecr delete-repository --repository-name ticketapp-api --force
aws amplify delete-app --app-id $AMPLIFY_APP_ID
aws logs delete-log-group --log-group-name $LOG_GROUP
aws iam delete-role-policy --role-name ticketapp-api-task --policy-name ticketapp-api
aws iam delete-role-policy --role-name ticketapp-ecs-exec --policy-name read-ticketapp-secrets
aws iam detach-role-policy --role-name ticketapp-ecs-exec \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam detach-role-policy --role-name ticketapp-ecs-infra \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices
for r in ticketapp-api-task ticketapp-ecs-exec ticketapp-ecs-infra; do aws iam delete-role --role-name $r; done
```

(The RDS-managed secret is deleted with the instance. The attachments
bucket is left as it was before this plan.)

## Open points before running this

1. Your OK to create resources (nothing above has been run).
2. Deployer permissions (section 2).
3. Checks on the first run:
   - Express Mode's handling of the extra security group;
   - the endpoint format and the ALB lookup;
   - the managed-secret rotation schedule;
   - whether the account's free tier or credits apply.
4. Later:
   - least-privilege DB role;
   - `DB_SSLMODE=verify-full` with the RDS CA bundle in the image;
   - a custom domain;
   - a shared rate-limit store if the service ever runs more than one
     task.
