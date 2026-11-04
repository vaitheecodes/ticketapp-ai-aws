# Deploying to AWS (stage A4 plan)

**Status: plan only. Nothing here has been run; no AWS resource exists
for it yet.** Region `ap-south-1` (Mumbai), CLI profile `ticketapp`.

**Chosen approach: demo-only hosting (section 0).** `scripts/aws_up.sh`
brings the stack up before a demo and `scripts/aws_down.sh` takes it down
after, keeping the database as an RDS snapshot. Sections 2-10 are the
same steps by hand: what the scripts do, and how to debug or change them.

Commands marked **💲** create something that bills (most of it by the
hour, whether or not anyone uses the app). Everything else is free (IAM,
security groups, configuration). Prices are on-demand ap-south-1 rates
from AWS's public Price List files (downloaded 3 Nov 2026; publication
dates: RDS 2026-10-29, ECS/Fargate 2026-10-19, VPC 2026-11-03, App Runner
2026-09-11).

## 0. Demo-only hosting (chosen): up before a demo, down after

The app is hosted only for demos. Running it all the time costs about
$60-70 a month (section 1), and almost all of that is billed while
nobody uses it. The plan is to create the hourly-billed parts before a
demo and delete them after, keeping the data in an RDS snapshot.

### Four ways to run it for demos only

ap-south-1 on-demand prices from the Price List files (downloaded 3 Nov
2026; EC2 file published 2026-10-29, Lightsail 2026-09-15). A **demo
day** below means the stack is up for 4 hours (30 min setup, the demo,
slack). The local CLI (aws-cli 2.36.19) has no `--cpu-architecture` for
Express Mode, so Fargate is priced as x86_64 (ARM64 is ~$0.011/hour
cheaper).

Price per hour for what (a), (b) and (d) run:

| Item | $/hour |
|---|---|
| Fargate task, x86_64, 0.5 vCPU / 1 GB (0.5 × 0.04256 + 1 × 0.004655) | 0.0259 |
| Public IPv4 on the task ($0.005 per address-hour, in use or idle) | 0.005 |
| ALB ($0.0239) + LCUs (a demo uses a fraction of one, ≤ $0.008) | 0.024-0.032 |
| ALB public IPv4, one per AZ (3 default subnets in ap-south-1) | 0.015 |
| RDS PostgreSQL db.t4g.micro Single-AZ | 0.021 |
| RDS storage, 20 GB gp3 ($0.131/GB-month) | 0.0036 |
| **Total while up** | **~0.10** |

| | (a) Always on (ECS Express) | **(b) On demand: scripted up/down, snapshot kept** | (c) One small instance, app + Postgres, stopped between demos | (d) (a), with parts stopped between demos |
|---|---|---|---|---|
| Per demo day (4 h) | $0 extra (already paid) | **~$0.41** | EC2 t4g.small: ~$0.07 (instance $0.0112/h + IPv4 $0.005/h). Lightsail 2 GB: ~$0.06 | ~$0.21 (task + its IP + RDS hours) |
| Idle month | ~$68-71 | **~$0.50 likely, ~$4.40 at most** | EC2: $1.82 (20 GB gp3 EBS at $0.0912), +$3.65 to keep an Elastic IP. Lightsail: the full $11.77 even when stopped; or snapshot ($0.05/GB-month) and delete | ~$32: ALB $17.45 + its 3 IPv4 $10.95 + RDS storage $2.62 + secrets $0.80. +$15.33 for any month RDS restarts itself |
| Time to get ready | none | ~25-35 min (RDS restore 10-20, service 5-10, frontend 2) | ~3-5 min (instance start) | ~10-15 min (RDS start, task start) |
| Same architecture as production | yes | yes | no | yes |
| Code or ops changes | none | none (scripts only) | yes, see below | none |

**Idle month for (b)**, what really bills while nothing runs:
- RDS snapshots, $0.095/GB-month: the script keeps the 2 newest.
  - If billing follows the data stored, that's a few MB for this app:
    cents.
  - If it follows the full 20 GB allocation, it's $1.90 per snapshot.
  - I couldn't confirm which from AWS's own pages; the $4.40 maximum
    assumes the second.
- App secret: $0.40 (prorated if deleted).
- ECR: about 0.15 GB per image tag built in CodeBuild (x86_64 only),
  about 0.4 GB for a two-architecture Docker build; the lifecycle rule
  keeps 5 tags, so $0.04-0.20.
- Build bucket: source zips of about 1 MB, deleted after 7 days: ~$0.
- Amplify (a few MB of static files), CloudWatch logs (14-day
  retention), IAM roles, security groups and the subnet group: ~$0, or
  free.
- No public IPv4 address, load balancer, NAT gateway or Elastic IP is
  left. The RDS-managed password secret is deleted with the instance.

**What (d) still bills** when the task count is 0 and RDS is stopped:
- the ALB with its IPv4 addresses (~$28/month), which is most of it;
- RDS storage, the secrets, and ECR.

Two catches: AWS starts a stopped RDS instance by itself after 7 days,
and then it bills at the full hourly rate until someone stops it again.
I also haven't confirmed that Express Mode accepts `minTaskCount` 0.

**What (c) needs** (code and operations changes):
- **SSL on the self-hosted Postgres.** In production the app refuses a
  `DB_SSLMODE` weaker than `require` (`db_postgres.production_problems`).
  Two ways around it:
  - turn on `ssl` in the Postgres container with a self-signed
    certificate (`require` doesn't verify it);
  - or add a code exemption for a database on localhost.
- **HTTPS for the API.** The Amplify site is HTTPS, so browsers block
  calls to a plain-HTTP API as mixed content.
  - Needs a reverse proxy with a certificate (Caddy or nginx with Let's
    Encrypt), and so a DNS name.
  - Without an Elastic IP ($3.65/month idle), the public IP, and with it
    that name, changes on every start.
  - Alternatively, serve the frontend from the same box.
- **Running it all yourself:** docker compose, OS patching, Postgres
  backups. A stopped EC2 instance doesn't snapshot itself.
- **Behind the proxy:** `TRUSTED_PROXY_HOPS=1`, and an instance role for
  Bedrock and S3.
- **Lightsail bills a stopped instance in full.** Only snapshot and
  delete saves money there.

**Recommendation: (b).**
- It is close to (c) on idle cost: about $0.50, versus $1.82 for (c).
- It needs no code change.
- It keeps the managed production architecture: RDS with SSL, ECS, an
  ALB with an AWS certificate, and least-privilege IAM. That is what the
  portfolio is about.
- It costs about $0.10 per hour while up.

The price is the 25-35 minutes of setup before each demo. Run
`aws_up.sh` well before the demo starts.

### How the frontend finds the API when its URL changes

- **The frontend URL never changes.** The Amplify app and its `main`
  branch are kept between demos, so `https://main.<appId>.amplifyapp.com`
  is fixed. That is the link to share, and it is also the backend's only
  CORS origin (`ALLOWED_ORIGINS`), so CORS never needs touching.
- **The API URL may change.** Express Mode builds the service URL from
  the service name and region (`<name>.ecs.<region>.on.aws`, per the ECS
  getting-started guide), so recreating `ticketapp-api` will probably
  give the same URL. AWS doesn't promise that.
- **`aws_up.sh` handles both cases.**
  - It records the API URL the frontend was built with on the Amplify
    branch, as the `API_URL` environment variable (free metadata).
  - It rebuilds and redeploys the frontend, with
    `VITE_API_URL=<api>/api`, only when that URL changed. That takes
    about 1-2 minutes and costs a fraction of a cent.
  - `FORCE_FRONTEND=1` forces a rebuild.
- **Cost: $0.** No code change either: `VITE_API_URL` is already a
  build-time setting (`frontend/src/api.js`).
- **Stable custom domain (not needed).** Route 53 costs $0.50/month per
  hosted zone plus about $3-15/year for a domain, and needs an ACM
  certificate on the ALB, which is free. It only pays off if the API URL
  turns out to change, or for a nicer link.
- **Rejected: proxying `/api` through Amplify** (a rewrite to the API
  URL). It would make the frontend same-origin, but:
  - Amplify's CloudFront origin timeout (30 s by default for CloudFront)
    may cut a long AI turn;
  - the client IP would then sit two proxies deep.

### The up/down procedure

Kept between demos (created by the first `aws_up.sh` run, cents a month):
- security groups `ticketapp-api` and `ticketapp-db`, and the DB subnet
  group;
- IAM roles `ticketapp-ecs-exec`, `ticketapp-ecs-infra` and
  `ticketapp-api-task`;
- the `ticketapp/app` secret, holding JWT_SECRET, ADMIN_PASSWORD and
  ADMIN_EMAIL;
- the ECR repository `ticketapp-api`;
- the image build (`scripts/aws_build_image.sh`): the bucket
  `ticketapp-build-<account id>`, the role `ticketapp-codebuild`, the
  CodeBuild project `ticketapp-image-build` and its log group;
- the Amplify app `ticketapp-web`;
- the RDS snapshots `ticketapp-db-<UTC time>`.

Created per demo (billed by the hour):
- the RDS instance `ticketapp-db`;
- the Express service `ticketapp-api`, with its Fargate task and ALB.

**Build the image** (no Docker needed, 3-5 min), once per commit you
want to demo. Commit first: the script refuses uncommitted or untracked
files, because the tag is the commit hash and ECR tags are immutable.

```bash
scripts/aws_build_image.sh
```

1. Checks the profile and that `git status --porcelain` is empty.
2. ECR repository `ticketapp-api`: created if missing (same settings and
   shared code as `aws_up.sh`: scan on push, immutable tags, keep the
   last 5).
3. If this commit's tag is already in ECR, says so and stops (exit 0).
4. Bucket `ticketapp-build-<account id>`: created if missing. Public
   access blocked, SSE-S3 encryption, objects deleted after 7 days.
5. Role `ticketapp-codebuild`: created if missing. Its policy, rewritten
   each run, allows only: writing its own log group, reading
   `source/*` in the build bucket, `ecr:GetAuthorizationToken`, and
   pushing to the `ticketapp-api` repository.
6. CodeBuild project `ticketapp-image-build`: created if missing.
   - `aws/codebuild/standard:7.0` (x86_64), `BUILD_GENERAL1_SMALL`,
     privileged mode (needed for `docker build`), 30 min timeout.
   - Inline buildspec: ECR login (token piped, never printed),
     `docker build -t <repo>:<tag> .`, `docker push`.
   - The build container's copy of the Dockerfile pulls `python:` from
     the ECR Public mirror of Docker's official images
     (`public.ecr.aws/docker/library/python`), to avoid Docker Hub's
     anonymous pull limit on CodeBuild's shared IPs. The repository's
     Dockerfile is unchanged.
   - Log group `/aws/codebuild/ticketapp-image-build`, kept 14 days.
   - The build gets no secrets: only the registry, repository and tag.
7. Uploads `git archive HEAD` (committed files only, so no `.env`),
   starts the build and waits. On failure it prints the failed phase and
   the last 30 log lines, with token-like strings redacted, and exits 1.
8. Prints the image tag and "Next: run scripts/aws_up.sh".

The image is linux/amd64 only. `aws_up.sh` reads the image manifest and
asks for ARM64 only when the image has an arm64 variant (a Docker buildx
build), so a CodeBuild image runs on x86_64.

**Cost:** CodeBuild `general1.small` is $0.005 per build minute in
ap-south-1 (Price List file published 2026-09-11), so a 3-5 minute
build is about 1.5-2.5 cents. The first 100 build minutes a month on
this size are listed as free tier. The bucket, logs and role cost
nothing measurable.

**Up** (25-35 min):

```bash
ADMIN_EMAIL=you@example.com scripts/aws_up.sh   # ADMIN_EMAIL only on the very first run
```

1. Checks the profile (`ticketapp`, `ap-south-1`).
2. Security groups and subnet group: created if missing.
3. App secret: generated on the first run and never printed.
4. Image: uses `IMAGE_TAG`, else this commit's tag.
   - If that tag isn't in ECR, it builds and pushes when Docker is
     running.
   - Otherwise it falls back to the newest pushed image, with a warning
     that `scripts/aws_build_image.sh` builds this commit.
   - With no image at all, it stops and points to
     `scripts/aws_build_image.sh`.
5. Database:
   - restored from the newest `ticketapp-db-*` snapshot;
   - or, on the first run, created empty (newest PostgreSQL 18.x,
     encrypted, private, deletion protection), with
     `--manage-master-user-password`.

   Either way RDS generates a new managed password secret. A restore
   can't ask for one: `restore-db-instance-from-db-snapshot` accepts
   `--manage-master-user-password` for Oracle only, and for postgres
   fails with `InvalidParameterValue`. So after a restore, once the
   instance is available, the script runs
   `aws rds modify-db-instance --manage-master-user-password --apply-immediately`
   and waits (up to 20 min, `DB_SECRET_WAIT` seconds) until the
   instance is available again **and** has `MasterUserSecret.SecretArn`;
   it can read "available" for a moment before the modify starts, so
   the status alone isn't enough. RDS sets a new password then; the old
   one from the snapshot stops working. A re-run is safe: an instance
   that already has the secret is never modified, and one that is
   still modifying is waited for, not modified again.
6. IAM roles: created if missing. The execution role's policy is
   rewritten each run for the two current secret ARNs.
7. Amplify app and branch: created if missing.
8. Express service:
   - created if missing;
   - updated if its image or DB secret reference changed (after a
     restore it always has);
   - then the script waits until the service is stable and `/health`
     returns 200;
   - ALB idle timeout set to 120 s, log retention to 14 days.
9. Frontend: rebuilt and deployed only if the API URL changed.
10. Smoke test without sign-in (`scripts/aws_smoke.py`). It fails if
    `/api/meta` reports 0 KB entries (or none: an image older than that
    field).

**Down** (10-20 min). Stop using the app first, then:

```bash
scripts/aws_down.sh
```

1. Shows what exists and asks you to type `yes`. Anything else changes
   nothing.
2. Snapshots the database and waits until the snapshot is available. If
   that fails, nothing is deleted.
3. Deletes the Express service and waits until it is inactive.
4. Removes deletion protection and deletes the DB instance. It skips a
   second snapshot (step 2 is the final one) and deletes the automated
   backups.
5. Deletes snapshots beyond the newest `KEEP_SNAPSHOTS` (default 2), only
   now that the new one exists.
6. Lists anything still billing by the hour: the service, the DB, any
   load balancer in the region, idle Elastic IPs.

Both scripts:
- print each step and stop at the first error;
- are safe to re-run, because every step checks what exists first;
- never print a secret (no `set -x`). Generated values go through a
  private temp directory that is removed on exit.

`tests/test_aws_scripts.py` runs all three scripts against a fake `aws`
CLI. The fake rejects any option the real subcommand doesn't accept
(`tests/fake_aws_options.json`, taken from the CLI's own offline help;
regenerate it with `python3 tests/aws_cli_options.py` after adding a
call), and a static test checks every `aws` call in the scripts against
the same list. Each script stops at step 1 if the profile is missing or
can't authenticate.

### Demo-day checklist

**The first time only** (the data then lives on in the snapshots):
1. Run `scripts/aws_build_image.sh`, then
   `ADMIN_EMAIL=you@example.com scripts/aws_up.sh`.
2. Copy the generated admin password to the clipboard without displaying
   it:
   ```bash
   aws --profile ticketapp secretsmanager get-secret-value --secret-id ticketapp/app \
     --query SecretString --output text \
     | python3 -c 'import json,sys; print(json.load(sys.stdin)["ADMIN_PASSWORD"], end="")' | pbcopy
   ```
3. Sign in as the admin and change the password. Keep the new one in
   your password manager. The secret's ADMIN_PASSWORD is only used to
   create the first admin.
4. Create the demo accounts. Production seeds no demo staff, so create
   them once:
   - **Team → Add:** a manager (with a department) and a technician.
     Each gets a temporary password and changes it on first sign-in.
     Sign in as each one once to do that.
   - **A customer:** sign up on the login page (no forced change).
   - Suggested emails: `demo-manager@…`, `demo-tech@…` and
     `demo-customer@…` on a domain you control.
   - Keep the passwords in your password manager, not in the repo.
   - Optionally seed a few tickets and one chat so the queues aren't
     empty.
5. Run `scripts/aws_down.sh`. From then on every demo starts from this
   data.

**Before each demo** (start 45 minutes ahead):
1. If you committed changes since the last demo, run
   `scripts/aws_build_image.sh` (it exits at once if the image exists).
   Then run `scripts/aws_up.sh` and wait for `[n] Up` and "All checks
   passed".
2. Run the signed-in checks (asks for the admin password, never echoed):
   ```bash
   python3 scripts/aws_smoke.py --frontend <frontend URL> --api <API URL> --admin
   ```
   It checks sign-in, `/api/auth/me`, and one ticket through Bedrock via
   the admin simulator (nothing saved).
3. In the browser, on the frontend URL:
   - sign in as `demo-customer`, start a chat, and raise a ticket with a
     small attachment (S3 upload) and download it again;
   - sign in as `demo-tech` and see the ticket in the queue;
   - sign in as `demo-manager` and open Incidents.
4. Keep the AWS console's Billing page open in another tab if you want
   to watch costs.

**After the demo:**
1. Run `scripts/aws_down.sh` and type `yes`.
2. Check that it ends with "No. What remains costs about $1-4 a month".
3. If it warns about a load balancer, check the EC2 console a few
   minutes later. Express Mode removes its ALB asynchronously.

**If you forget to run down:** about $2.45 a day. Set a monthly AWS
Budget (for example $10, with email alerts) as a safety net. A plain
cost budget with email alerts costs nothing.

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

(Option (a) in section 0. ARM64 shown; on x86_64, which the current
CLI gives you, add ~$8.)

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
- CodeBuild (create project, start build, read builds) and S3 (create
  and configure the build bucket, upload to it), for
  `scripts/aws_build_image.sh`
- IAM: create roles and `iam:PassRole` for the three roles below and
  `ticketapp-codebuild`
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
ADMIN_EMAIL=you@example.com python3 - "$SECRET_FILE" <<'PY'
import json, os, secrets, sys
json.dump({"JWT_SECRET": secrets.token_hex(32),
           "ADMIN_PASSWORD": secrets.token_urlsafe(18),
           "ADMIN_EMAIL": os.environ["ADMIN_EMAIL"]}, open(sys.argv[1], "w"))
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

Without Docker, `scripts/aws_build_image.sh` does the build in CodeBuild
instead (section 0, "Build the image"); the result is x86_64 only.

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
| `TRUSTED_PROXY_HOPS` | `1` | client IP behind the ALB |
| `EMAIL_ENABLED` | `false` | no SMTP in this plan |
| secret `JWT_SECRET` | `$APP_SECRET_ARN:JWT_SECRET::` | |
| secret `ADMIN_EMAIL` | `$APP_SECRET_ARN:ADMIN_EMAIL::` | first start only (kept in the secret so `aws_up.sh` needs it once) |
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
      {\"name\":\"TRUSTED_PROXY_HOPS\",\"value\":\"1\"},
      {\"name\":\"EMAIL_ENABLED\",\"value\":\"false\"}],
    \"secrets\":[
      {\"name\":\"JWT_SECRET\",\"valueFrom\":\"$APP_SECRET_ARN:JWT_SECRET::\"},
      {\"name\":\"ADMIN_EMAIL\",\"valueFrom\":\"$APP_SECRET_ARN:ADMIN_EMAIL::\"},
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

## 10. Smoke test, then full teardown

**Smoke test:** `scripts/aws_smoke.py` (section 0, demo-day checklist).
The container log starts with `KB: <n> entries loaded from /app/data/kb`;
in production the API refuses to start with 0 (`STARTUP CONFIG ERROR:
... has no KB entries`), and `/api/meta` shows `kb_entries`.
By hand:
1. Sign in as the admin and change the password.
2. Create a technician.
3. As a customer, sign up, start a chat (Bedrock reply), raise a ticket
   with an attachment (S3 round trip), and download it.
4. Six wrong passwords for one account give 429.

**Full teardown**, to leave AWS entirely. Between demos use
`scripts/aws_down.sh` instead. This also removes what it keeps. The
final snapshot keeps the data for $0.095/GB-month; delete it too
(`aws rds delete-db-snapshot`) to reach $0.

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
   - the endpoint format (the scripts accept it with or without
     `https://`);
   - whether a recreated service gets the same URL (the frontend is
     rebuilt if not);
   - whether the ALB disappears when the last Express service is deleted
     (`aws_down.sh` warns if one is left);
   - that `restore-db-instance-from-db-snapshot --manage-master-user-password`
     creates a new managed secret as expected;
   - the managed-secret rotation schedule (less relevant now: each
     restore gets a new secret, and the stack rarely lives 7 days);
   - whether the account's free tier or credits apply;
   - the first CodeBuild image build: that `aws/codebuild/standard:7.0`
     is still offered, that the ECR Public `python` mirror has the same
     tag, that the push works with only the listed ECR actions, and how
     long a build takes.
4. Later:
   - least-privilege DB role;
   - `DB_SSLMODE=verify-full` with the RDS CA bundle in the image;
   - a custom domain;
   - a shared rate-limit store if the service ever runs more than one
     task.
