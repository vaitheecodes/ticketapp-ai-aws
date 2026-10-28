# AWS plan (ticketapp-ai-aws)

This copy moves the help desk to AWS. The original `ticketapp-ai` folder
stays the Catalyst version. Only stage A1 (below) is built so far; the
Catalyst-specific code (QuickML-on-AppSail provider, AppSail build,
`catalyst/`) stays in place until the AWS replacements pass the tests.

## Target

| Concern | Today (Catalyst branch) | AWS target | Status |
|---|---|---|---|
| AI provider | `quickml_appsail` (GLM on QuickML), `mock` locally | **Amazon Bedrock** provider (`AI_PROVIDER=bedrock`, Converse API) behind the existing `backend/ai_service` interface (same validation, retry and fail-safe rules) | Built (A1); model quality open, see below |
| Attachments | Local disk (`db.uploads_dir()`) | **Amazon S3** behind a storage interface; private bucket, authorised download endpoint only (no public links) | Planned |
| Database | SQLite | Hosted database: **RDS PostgreSQL or DynamoDB** | **Decide later** (atomic ticket claim is the hard requirement) |
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

1. Database: RDS PostgreSQL vs DynamoDB.
2. Backend and frontend hosting.
3. Bedrock model (region: `ap-south-1`, APAC inference profiles).

## AWS setup (done)

- IAM user `ticketapp-dev` (CLI profile `ticketapp`), policy
  `ticketapp-dev-policy`: Bedrock list/invoke, the attachments bucket.
- Private bucket `ticketapp-ai-attachments-<account id>` in `ap-south-1`:
  public access blocked, default encryption on. Not used by the app yet.

## Stage A1: Bedrock provider (Oct 2026)

`backend/ai_service/backends.py:BedrockBackend`; settings
`BEDROCK_MODEL_ID` (default `apac.amazon.nova-lite-v1:0`), `AWS_REGION`,
`AI_MAX_TOKENS`; credentials from the default AWS chain only.

First run of `scripts/bedrock_quality_check.py` with Nova Lite (28 Oct
2026): **4/10 prompts valid**. Chat answer, clarifying question,
continuation check and suggested steps passed. Both categorisations, the
escalation summary, incident correlation, incident summary and
post-incident draft were rejected twice (16 calls, 9,469 input / 2,816
output tokens, ~16 s total, ~$0.0012). The raw replies weren't saved on
that run, so the cause isn't known yet (the categorise replies were ~40
tokens where a valid one is ~15, which suggests the schema is being
echoed or wrapped). Next: re-run with `--save-rejected`, then fix the
prompt or use Bedrock tool use for structured output, and compare a
stronger model before choosing.
