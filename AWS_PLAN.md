# AWS plan (ticketapp-ai-aws)

This copy moves the help desk to AWS. The original `ticketapp-ai` folder
stays the Catalyst version. Only stage A1 (below) is built so far; the
Catalyst-specific code (QuickML-on-AppSail provider, AppSail build,
`catalyst/`) stays in place until the AWS replacements pass the tests.

## Target

| Concern | Today (Catalyst branch) | AWS target | Status |
|---|---|---|---|
| AI provider | `quickml_appsail` (GLM on QuickML), `mock` locally | **Amazon Bedrock** provider (`AI_PROVIDER=bedrock`, Converse API) behind the existing `backend/ai_service` interface (same validation, retry and fail-safe rules) | Built (A1); model quality open, see below |
| Attachments | Local disk (`db.uploads_dir()`) | **Amazon S3** (`STORAGE_BACKEND=s3`) behind a storage interface; private bucket, authorised download endpoint only (no public links) | Built (A3); round trip verified against the real bucket |
| Database | SQLite | **RDS for PostgreSQL** (`DB_BACKEND=postgres`) | Built and tested locally on PostgreSQL 18 (A2); no RDS instance yet |
| Backend host | AppSail | **Decide later** (e.g. App Runner, ECS Fargate, Lambda) | Open |
| Frontend host | Slate (planned) | **Decide later** (e.g. S3 + CloudFront, Amplify) | Open |

## Rules carried over

- No secrets in code or files; configuration from environment variables
  (later: Secrets Manager / SSM Parameter Store).
- Keep every feature and permission rule working; the pytest suite is the
  safety net. Mock AI provider for tests and local runs.
- Our own JWT login stays the only authority for authorization.
- Do not remove Catalyst code until its AWS replacement passes the tests.

## Open decisions (ask before implementing)

1. Database: RDS PostgreSQL (chosen in A2). Still open: instance size,
   Multi-AZ, and RDS Proxy vs the app's own pool.
2. Backend and frontend hosting.
3. Bedrock model: Nova Lite for now (see stage A1); revisit after
   reviewing real outputs.

## AWS setup (done)

- IAM user `ticketapp-dev` (CLI profile `ticketapp`), policy
  `ticketapp-dev-policy`: Bedrock list/invoke, the attachments bucket.
- Private bucket `ticketapp-ai-attachments-<account id>` in `ap-south-1`:
  public access blocked, default encryption on. Used by `STORAGE_BACKEND=s3`
  (objects under `attachments/`).

## Stage A1: Bedrock provider (Oct 2026)

`backend/ai_service/backends.py:BedrockBackend`; settings
`BEDROCK_MODEL_ID` (default `apac.amazon.nova-lite-v1:0`), `AWS_REGION`,
`AI_MAX_TOKENS`; credentials from the default AWS chain only.

First run of `scripts/bedrock_quality_check.py` with Nova Lite (28 Oct
2026, JSON asked for in text): **4/10 prompts valid**. Chat answer, clarifying question,
continuation check and suggested steps passed. Both categorisations, the
escalation summary, incident correlation, incident summary and
post-incident draft were rejected twice (16 calls, 9,469 input / 2,816
output tokens, ~16 s total, ~$0.0012). The raw replies weren't saved on
that run, so the cause isn't known yet (the categorise replies were ~40
tokens where a valid one is ~15, which suggests the schema is being
echoed or wrapped).

Diagnosis (`--save-rejected`, same result): every rejected reply was
**schema-shaped**: the answer nested under `"properties"` next to
`"required"` / `"title"` / `"type"` (categorise, incident correlation,
incident summary, post-incident draft), values put inside the schema's
field definitions (escalation summary), or the schema returned with no
answer at all (two retries). Fences and preamble appeared too but were
already stripped. Not truncation (no reply hit max tokens) and not wrong
keys otherwise.

Fix: JSON operations on Bedrock now use Converse **tool use**: the
operation's pydantic schema is the input schema of one tool and
`toolChoice` forces it; the tool input is the JSON reply. Validation and
the one retry stay as the backstop. Text replies are unchanged.

| Run (28 Oct 2026, 10 prompts) | Valid | Calls | Latency (sum) | In / out tokens | Cost |
|---|---|---|---|---|---|
| Nova Lite, JSON in text | 4/10 | 16 | 14.6 s | 9,469 / 2,464 | $0.00137 |
| Nova Lite, tool use | **10/10** | 10 | 9.2 s | 9,753 / 1,019 | $0.00098 |
| Nova Pro, tool use | **10/10** | 10 | 11.7 s | 9,753 / 1,017 | $0.01299 |

Prices: on-demand, Asia Pacific (Mumbai), from AWS's public Price List
for Bedrock (published 2026-10-28): Nova Lite $0.071 / $0.284 and Nova
Pro $0.94 / $3.76 per 1M input / output tokens. Pro is ~13x the cost and
~25% slower for the same pass rate here.

Decision: keep `BEDROCK_MODEL_ID` = Nova Lite as the single default.
A per-task model setting (e.g. a stronger model only for incident
correlation / summaries) is an option if a review of real outputs shows
Lite's wording is weaker; not built.

## Stage A2: SQLite -> PostgreSQL (Oct 2026)

No AWS resources were created. `DB_BACKEND=postgres` selects
`backend/db_postgres.py`; SQLite stays the default.

- **Version:** RDS supports PostgreSQL 14-18 (AWS release calendar; 18.6
  is the newest minor). Tested on Homebrew **PostgreSQL 18.3**, the same
  major. Availability in ap-south-1 wasn't checked from the CLI (the
  `ticketapp` user has no `rds:Describe*` permission).
- **Library:** psycopg 3 + psycopg_pool (SQLAlchemy would mean rewriting
  the hand-written SQL for no gain).
- **Approach:** an adapter runs the existing SQL on Postgres; targeted
  repositories for the claim, account lookup and customer-visible
  threads. Migrations: `migrations/postgres/`, `scripts/db_migrate.py`.
- **Pool safety:** DB_POOL_MAX (10) and DB_POOL_TIMEOUT (10 s); 503 when
  exhausted. No connection is held during AI calls, except one case:
  with an embeddings model configured, incident correlation embeds new
  tickets while its read connection is open (Bedrock has no embeddings
  configured, so it doesn't happen today).
- **Concurrency:** 20 simultaneous claims of one ticket -> exactly one
  winner, on SQLite and Postgres. 8 chat turns through a 2-connection
  pool all finish.
- **Before RDS:** pick DB_POOL_MAX x instances below the instance's
  max_connections; use `DB_SSLMODE=require` (or verify-full with the RDS
  CA); keep the password in Secrets Manager; run `scripts/db_migrate.py`
  as a deploy step.

## Stage A3: attachments on S3 (Oct 2026)

`backend/storage.py`: `local` (default) or `s3`, selected by
`STORAGE_BACKEND`. Settings `S3_BUCKET`, `S3_PREFIX` (`attachments/`),
`AWS_REGION`; credentials from the default AWS chain only.

- Same validation as before, run before anything is stored; a failed
  upload deletes what it put and rolls back the rows.
- Only metadata in the database. Objects written with SSE-S3 (AES256).
- Downloads only through the authorised endpoint, which streams the bytes
  after the access check. **No presigned URLs**: nothing to leak, and the
  10 MB limit keeps streaming through the app cheap.
- S3 errors map to our own messages and a 503; AWS error text is never
  logged.
- Tests use a fake S3 client. `scripts/s3_storage_check.py --yes` was run
  once against the real bucket (29 Oct 2026): put/get/delete of a 35-byte
  object, about 1.1 s in total; nothing left in the bucket.
- For the deployed role: `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`
  on `arn:aws:s3:::<bucket>/attachments/*` only (the CLI user's policy is
  broader: the whole bucket).
