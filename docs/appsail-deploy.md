# Deploying the backend to Catalyst AppSail (Phase 4)

Walking-skeleton setup: SQLite on AppSail's local disk (data may be lost on
restart or redeploy until the Data Store stage). No secrets in this file.

## Build and deploy

```bash
scripts/build_appsail.sh
```
```bash
cd catalyst && catalyst deploy
```

- `scripts/build_appsail.sh` writes `build/appsail/`: `backend/`, the KB JSON
  (`data/kb/kb_entries.json`), `requirements-runtime.txt`'s packages as
  Linux x86-64 / CPython 3.13 wheels (AppSail installs nothing itself), and
  `app-config.json` (stack `python_3_13`, memory 512 MB, start command
  `sh -c 'python3 -m uvicorn backend.main:app --host 0.0.0.0 --port ${X_ZOHO_CATALYST_LISTEN_PORT:-9000}'`).
- `app-config.json` deliberately has **no `env_variables`**: the Catalyst docs
  don't say whether `catalyst deploy` replaces the console's variables with
  that list, and after a deploy whose file set only `APP_ENV` the service
  stopped starting. All variables live in the console.
- `catalyst/catalyst.json` points the CLI at `../build/appsail` (service
  `ticketapp-api`); `catalyst/.catalystrc` comes from `catalyst init` and is
  git-ignored.

## Required console variables

ticket-ai → environment (Development / Production) → Serverless → AppSail →
ticketapp-api → Configuration → Environment Variables. Variables are stored
per environment.

| Name | Value | Notes |
|---|---|---|
| `APP_ENV` | `production` | Required on AppSail: the app refuses to start without it |
| `JWT_SECRET` | 32+ random characters (`openssl rand -hex 32`) | Secret. Missing, the dev default, or shorter than 32 → refuses to start. Rotating it logs everyone out |
| `ADMIN_EMAIL` | the single admin's email | Needed only until the admin exists (missing then → refuses to start) |
| `ADMIN_PASSWORD` | a strong password | Secret; same rule as `ADMIN_EMAIL`; never overwrites an existing admin |
| `AI_PROVIDER` | `quickml_appsail` | Without it the app uses the mock provider (placeholder replies) |
| `QUICKML_ENDPOINT_KEY` | the endpoint key for this environment | Secret |
| `QUICKML_ENDPOINT_NAME` | e.g. `glm-flash-47` | From the endpoint URL `.../genai/endpoints/<name>/generate` |
| `QUICKML_ORG_ID` | your Catalyst org id | Sent as `CATALYST-ORG`. Names containing `CATALYST` are rejected by the console ("reserved keywords") |
| `EMAIL_ENABLED` | `false` | Real email off |

Optional: `ALLOWED_ORIGINS` (the frontend origin, once there is one),
`INCIDENT_*`, `AI_TIMEOUT_SECONDS`, `DB_PATH` (leave unset for now).

## After every deploy

1. `GET /health` → `{"ok": true}`. A 503 "Execution failed. Please check the
   startup command or port" means startup failed: the AppSail log has one
   line `STARTUP CONFIG ERROR: <VARIABLE> <problem>; refusing to start.`
   naming the variable (never its value).
2. Log in as admin and call `GET /api/admin/diag/config` (temporary): which
   required variables are set, as booleans only.
3. Re-check the console list — it is not known whether a deploy keeps it.

## Known facts (verified on AppSail, Oct 2026)

- AppSail injects `X-ZC-*` headers (incl. an admin credential) into every
  request, and does **not** strip client-sent `X-ZC-*` headers: it adds its
  own value next to the client's. The app ignores the credential when any
  `X-ZC-*` header is duplicated, and never uses these headers for
  authorization (our JWT is the only authority).
- Bundle: ~57 MB, ~1,900 files (≈50 MB is LangGraph and its dependencies,
  23 MB of that `zstandard`). Default disk is 256 MB.
- Cold start: ~3.4 s for the first request after idle; then ~0.13 s for
  `/health`. A deploy takes ~18 s.
- The development URL (`https://<service>-<id>.development.catalystappsail.in`)
  is public, with no platform login.
