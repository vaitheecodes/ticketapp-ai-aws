# AWS plan (ticketapp-ai-aws)

This copy moves the help desk to AWS. The original `ticketapp-ai` folder
stays the Catalyst version. Nothing below is implemented yet; the
Catalyst-specific code (QuickML-on-AppSail provider, AppSail build,
`catalyst/`) stays in place until the AWS replacements pass the tests.

## Target

| Concern | Today (Catalyst branch) | AWS target | Status |
|---|---|---|---|
| AI provider | `quickml_appsail` (GLM on QuickML), `mock` locally | **Amazon Bedrock** as a new provider behind the existing `backend/ai_service` interface (same validation, retry and fail-safe rules) | Planned; model choice later |
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
3. Bedrock model and region.
4. New GitHub repository for this copy (no remote is configured yet).
