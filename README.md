# ticketapp-ai

An AI-powered help desk for a monitoring product (Site24x7 support
topics), inspired by Zoho Desk and AI customer-experience platforms such
as Thunai.ai, Zendesk AI and Intercom Fin — plus the data pipeline that
builds its knowledge base.

- **Customers** chat with an AI assistant grounded in a 386-entry
  knowledge base, or raise email-style tickets with attachments.
- **The AI** answers what it can, asks one targeted question when unsure,
  and hands everything else to people with a summary and suggested next
  steps, so nobody has to repeat themselves.
- **Technicians** claim tickets from an unassigned queue, reply to the
  customer, and keep private internal notes.
- **Managers** review AI-detected *incidents* (several customers reporting
  what may be one problem) for their department, and oversee workload.
- **The admin** manages accounts, departments, AI routing settings and
  reads the audit log.

The original data-engineering layer (bronze → silver → gold) is described
[further down](#data-layer).

## Contents

- [Architecture](#architecture)
- [Setup and startup](#setup-and-startup)
- [Environment variables](#environment-variables)
- [Database and migrations](#database-and-migrations)
- [AI configuration](#ai-configuration)
- [Roles and workflows](#roles-and-workflows)
- [API](#api)
- [Testing](#testing)
- [Features: completed and tested](#features-completed-and-tested)
- [Known limitations](#known-limitations)
- [Data layer](#data-layer)

## Architecture

```
React (Vite) — frontend/            one login page; portals per role:
  /customer  /technician  /manager  /admin
        │  fetch /api/*  (JWT bearer)
        ▼
FastAPI — backend/
  main.py            auth, customer tickets, attachments, /meta
  chats.py           AI chat sessions (separate from tickets)
  staff_tickets.py   technician workspace, manager overview
  incidents.py       AI incident correlation + manager review
  admin.py           accounts, departments, settings, audit log
  ticket_lifecycle.py / audit.py / app_settings.py
        │
        ├── graph.py        LangGraph routing pipeline
        │     retrieve KB → classify → decide route
        │       → answer | ask a clarifying question | hand to a human
        ├── ai_service/     every model call: one module per operation,
        │                   pydantic-validated input/output (Azure OpenAI)
        └── kb_retrieval.py TF-IDF retrieval over the 386 KB entries
        ▼
SQLite (DB_PATH) + uploads/ next to it
```

Stack: Python 3.10, FastAPI, LangGraph, Azure OpenAI (`openai`
package), SQLite, bcrypt + JWT · React 19, Vite, React Router · pytest.

## Setup and startup

Prerequisites: Python 3.10, Node 20+, an Azure OpenAI chat deployment.

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Fill in at least the `AZURE_OPENAI_*` values and `JWT_SECRET` (see
[Environment variables](#environment-variables)). Then run the backend
from the repo root:

```bash
uvicorn backend.main:app --reload
```

and the frontend in a second terminal:

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to `:8000`.

On first start the backend creates the database, seeds the default
departments (covering all 17 categories) and a small mock `orders` table,
and creates the admin account:

- **Admin**: from `ADMIN_EMAIL` / `ADMIN_PASSWORD`. If they're unset in
  development, a fallback admin is created and a warning with the details
  is logged (`backend/auth.py`). In production (`APP_ENV=production`) the
  server refuses to start without them.
- **Demo staff** (development only, never in production): a manager
  (Infrastructure department) and a technician; credentials in
  `backend/seed_data.py`.
- **Customers** sign up on the login page (or the admin creates them).

The knowledge base reads `data/silver/{tickets,resolutions}.parquet`
(committed). Regenerate them with the [data pipeline](#data-layer) if
needed.

**Deployment (Azure).** The backend runs on App Service (`Procfile`,
`runtime.txt`); set the variables below as Application Settings, with
`DB_PATH=/home/data/ticketapp.db` so the database and uploads persist,
`APP_ENV=production`, `ADMIN_*`, and `ALLOWED_ORIGINS` set to the frontend
origin. The frontend builds to Static Web Apps with
`VITE_API_URL=https://<app>.azurewebsites.net/api` set at build time
(`frontend/.env.example`).

**Continuous deployment.** Two GitHub Actions workflows deploy on push to
`main`: `.github/workflows/deploy-backend.yml` (changes under `backend/`,
`data/silver/`, `requirements.txt`, `runtime.txt`; packages committed files
with `git archive` and deploys to the `ticketapp-ai-api` App Service) and
`.github/workflows/deploy-frontend.yml` (changes under `frontend/`; builds
with Node 22 and deploys `frontend/dist` to the Static Web App). Both can
also be run manually (Actions → workflow → Run workflow). They need two
repository secrets: `AZURE_WEBAPP_PUBLISH_PROFILE` (App Service → Download
publish profile; requires SCM Basic Auth Publishing Credentials to be on)
and `AZURE_STATIC_WEB_APPS_API_TOKEN` (Static Web App → Manage deployment
token).

## Environment variables

All are documented in `.env.example`. Never commit real values.

| Variable | Default | Purpose |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_DEPLOYMENT` | — | Chat model for every AI operation |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | unset | Optional: embeddings for incident grouping; unset = group by department/category |
| `JWT_SECRET` | dev value | Signs session tokens; use a long random value in production |
| `APP_ENV` | `development` | `production` requires `ADMIN_*` and skips demo staff |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | — | The single admin, created once if no admin exists (never overwritten) |
| `DB_PATH` | `backend/ticketapp.db` | SQLite file; attachments go to `uploads/` beside it |
| `ALLOWED_ORIGINS` | localhost dev ports | CORS origins, comma-separated |
| `INCIDENT_WINDOW_HOURS` | 12 | Reports considered together |
| `INCIDENT_MIN_TICKETS` | 3 | Group size before asking the AI (from 2+ customers) |
| `INCIDENT_SIMILARITY` | 0.80 | Embedding similarity split (with embeddings only) |
| `INCIDENT_ROUTING_CONFIDENCE` | 0.6 | Below this, an incident is "Needs routing" |
| `INCIDENT_MEMORY_DAYS` | 90 | How long resolved incidents are used to answer "was there an outage?" |
| `EMAIL_ENABLED` | `false` | Real email is off by default; the email-style thread is in-app |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS` | — | Used only when `EMAIL_ENABLED=true` |
| `VITE_API_URL` (frontend) | `/api` | API base for production builds |

The `INCIDENT_*` values are starting points: the admin can override them
(and the AI routing policy, and status labels) in **Admin → Settings**;
saved values win.

## Database and migrations

SQLite, created and migrated by `db.init_db()` on every startup — no
separate migration step. Migrations are in place and non-destructive
(columns added, data moved, never dropped), and safe to run repeatedly:

- legacy `agents` → `staff` (technicians), `assigned_agent_id` →
  `assigned_technician_id`
- legacy statuses `awaiting_reply` → `pending_customer`, `assigned` →
  `in_progress`
- pre-session chat tickets → `chat_sessions` / `chat_messages` (escalated
  ones stay tickets, linked to the chat)
- legacy system messages → customer-visible system events
- manager → department many-to-many → one department per manager (first
  mapping kept)

Main tables: `customers`, `staff`, `admins`, `departments`, `tickets`,
`ticket_messages` (with `visibility`), `attachments`,
`ticket_status_history`, `ticket_assignment_history`, `chat_sessions`,
`chat_messages`, `chat_reviews`, `incident_clusters`, `incident_tickets`,
`incident_departments`, `incident_reviews`, `incident_deliveries`,
`incident_memory`, `audit_events`,
`app_settings`. Schema and comments: `backend/db.py`.

## AI configuration

Every model call goes through `backend/ai_service/` (one module per
operation: categorize, chat answer / clarify / follow-up check,
conversation and ticket summaries, suggested steps, incident correlation
incident summary, post-incident summary draft, incident-question parsing):

- **Validated output**: each operation has a pydantic schema; output is
  checked (plus semantic checks, e.g. correlated ticket ids must be from
  the input) and retried once, then the caller treats the AI as
  unavailable.
- **AI failures never change state**: a chat or ticket is never resolved,
  closed or escalated because a call failed; the customer's message is
  kept, and raising a ticket always works.
- **Prompt-injection defence**: instructions in the system message,
  customer text in delimited data blocks the customer can't close, and the
  model is told to treat them as data. Internal notes never reach a
  prompt; incident analysis sees customers only as anonymous labels.
- **Grounding**: answers and suggested steps use the closest KB entries;
  prompts forbid inventing results or claiming to have checked systems
  the app isn't connected to.
- **Routing policy** (`graph.decide_route`, editable in Admin →
  Settings): escalation language (abuse, negation, long compound
  messages) → human; high-risk categories (ACCOUNT, DATA) below 90%
  confidence → human; other categories below 75% → up to 3 clarifying
  questions, then human; otherwise the AI answers.

## Roles and workflows

| Role | Signs in at | Can |
|---|---|---|
| Customer | `/login` → `/customer` | chat with the AI, raise tickets with attachments, follow up, reopen, see chat history |
| Technician | → `/technician` | unassigned / mine / recent / open queues, claim, reply, internal notes, status, AI hand-off panel, chat reviews, incidents assigned to them (resolve & send summary) |
| Manager (one department) | → `/manager` | incidents for their department (confirm / investigate / dismiss, correct departments, assign a technician, resolve & send the post-incident summary), department tickets and workload (read-only) |
| Anyone | `/incidents` | public incident history (no login) |
| Admin (single account) | → `/admin` | all tickets and reassignment, team and customer accounts, departments, settings, incidents, activity/audit log, pipeline simulator |

**Chat.** A chat is not a ticket. If the customer confirms it's solved, it
closes with an AI summary and an internal review record (no ticket). If it
needs a person — the customer asks, the AI can't do it, the routing
policy says so, or the customer clicks *Raise a ticket* — an unassigned
Open ticket is created with the transcript linked, an AI summary and
suggested next steps; the customer gets the ticket number.

**Tickets.** Statuses: Open, In Progress, Pending Customer, Resolved,
Closed (labels configurable). Claiming is atomic (two simultaneous claims:
one wins, one gets 409). Every status and owner change is recorded in
history and the audit log. Customer replies return Pending Customer and
Resolved tickets to Open.

**Incidents.** After each new ticket or escalation, recent tickets from
the same component area and at least two customers are sent to the AI,
which decides whether they share an incident, separating observed facts
from hypotheses. Clusters are reused for an ongoing event, routed to the
owning department only when the AI is confident enough, and never close
or merge tickets. Once a manager confirms one, affected customers see a
neutral "we're aware of this issue" notice.

**Resolving an incident.** Only a confirmed incident can be resolved, by
a manager or the technician it's assigned to: they enter what was done,
when the fix was applied (IST) and, optionally, the root cause. The AI
drafts a customer summary (what happened, what was fixed, what to do) and
an internal one from the incident's facts and that note only; code adds
the start–resolution times and the cause line (the staff-entered cause,
or "still being reviewed"), and a check rejects any model text with a
time, date or cause claim. Staff edit, preview and *Approve & send* —
never automatic. The summary is posted on every linked ticket as a public
reply from the Support Team (and emailed when `EMAIL_ENABLED=true`), with
a delivery record per ticket: sending twice never duplicates, a failing
ticket doesn't block the others and can be retried. Linked tickets are
marked Resolved only if staff tick the box.

**"Was there an outage?"** Confirmed and resolved incidents are kept in
`incident_memory` (category, times, approved summary, affected customer
ids, visibility, optional embedding) for `INCIDENT_MEMORY_DAYS`. In a
chat or an AI-handled ticket, a message that looks like a question about
an outage (keyword gate, then the AI turns it into an absolute IST time
window and topic) is answered from that memory with fixed templates: an
affected customer hears "Yes — you reported this on … (ticket …)" and the
approved summary; anyone else hears about it only if staff made it
public; otherwise "no known incident matched" and an offer to raise a
ticket. Unresolved incidents are described as being investigated, with no
cause. The model never writes the answer, so it can't invent an incident
or reveal another customer's details.

## API

64 endpoints, grouped by role, with request and response formats:
**[docs/API.md](docs/API.md)**. A running server also serves Swagger at
`/docs`.

## Testing

```bash
pytest tests/
```

```bash
cd frontend && npm run build && npm run lint
```

The pytest suite covers the data pipeline and the help desk; tests never
call the real AI provider (`tests/conftest.py` disables it; individual
tests script the model's replies). Highlights:

| File | Covers |
|---|---|
| `test_e2e_helpdesk.py` | the full journey: chat → escalation → technician claim, reply + private note → customer view → correlated incident → manager confirms (real pipeline, scripted model) |
| `test_roles_auth.py` | admin bootstrap, roles, login routing, forced password change, departments |
| `test_chat_sessions.py`, `test_chat_followup_gates.py`, `test_handoff_escalation.py` | chat resolution vs escalation, AI-failure handling, review records, migration |
| `test_ticket_workspace.py` | claim race, public vs internal visibility, history, manager scope |
| `test_ai_service.py` | schema validation and retry, provider failure, injection containment, no secrets in logs |
| `test_incidents.py` | clustering, time window, pre-filters, dedupe, routing, review, banner |
| `test_incident_resolution.py` | resolve only when confirmed, facts-only draft (invented times/causes rejected), idempotent fan-out, per-ticket failure isolation, email gate, "was there an outage?" answers (linked / unlinked / public / expired, no cross-customer leakage), AI failure sends nothing |
| `test_admin_console.py` | account administration, settings, audit log, email gate, attachment access |
| `test_customer_tickets.py` | ticket model, email-style replies, attachments |
| `test_data_quality.py` | 33 checks over the pipeline output |

The frontend has no automated tests; it was verified by building, linting
and clicking through each portal in a browser at desktop and phone widths.

## Features: completed and tested

- Single login with role routing; customer signup; one env-configured
  admin; admin-created staff and customers with forced password change;
  deactivation that blocks live sessions.
- AI chat with KB grounding, clarifying questions, follow-up handling,
  resolution summaries + internal review, escalation to an unassigned
  ticket with summary, suggested steps and linked transcript.
- Email-style tickets with attachments (validated, private storage,
  authorised download), replies, reopen, unread markers.
- Technician workspace: queues, search/filters, atomic claim, public
  replies vs private notes (filtered in SQL for customers), status
  changes, history, AI hand-off panel.
- Manager department oversight and workload; AI incident correlation
  with confidence, facts vs hypotheses, department routing, Needs
  routing, review with notes, customer notice on confirmed incidents.
- Post-incident summaries (AI draft, staff edit, approve & send to every
  linked ticket, idempotent with per-ticket delivery status), incident
  memory answering "was there an outage?", public incident history page.
- Admin console: tickets, team, customers, departments, settings (routing
  policy, incident settings, status labels), incidents, audit log.
- Modular, validated AI service; graceful AI failure everywhere.

## Known limitations

- **SQLite, one process.** Settings are cached per process, and incident
  correlation runs as an in-process background task. Fine for the
  single-process App Service setup, but needs work for several workers or
  heavy load.
- **The negation rule is broad.** Any "no"/"not" sends a message to a
  person, so most long ticket descriptions skip the AI. Admins can turn
  it off in Settings.
- **Chats are never marked abandoned automatically;** an idle chat stays
  active and can be resumed.
- **Incident correlation looks at tickets only** (not AI-resolved chats).
  Without an embeddings deployment, grouping is by department/category,
  and the AI does the fine-grained judgement.
- **The ticket form has no separate contact email/phone or product
  fields;** the account's name and email and the category are used.
- **Composer formatting** is a Markdown subset (bold, code, lists), not a
  WYSIWYG editor. Only customers can attach files.
- **No manager reassignment and no "reply to all linked tickets"** (the
  spec makes both optional; neither existed before).
- **Real email is send-only and off by default;** there's no inbound
  email.
- **Session tokens** live in `localStorage` with no refresh tokens. Failed
  logins are audited but not rate-limited.
- **Legacy data:** admin accounts created by the old public admin signup
  are kept (a warning is logged); remove extras manually.
- **The `src/agent/` evaluation track** (below) is still a stub and
  separate from the live app.

## Data layer

**Source:** `data/raw/Site24x7_Customer_Support_Training_Dataset_27K.csv`
— 26,872 rows, 27 intents under 17 categories, all genuine Site24x7
monitoring-product support topics. It's an instruction-tuning dataset
(`flags, instruction, category, intent, response`), so the silver stage
layers realistic synthetic operational metadata on top of the genuine
support language. **The real knowledge-base size is 386 unique
instructions** — each is duplicated ~5x under different `flags` values (a
dataset-generation artifact), so every ticket is tagged with its
canonical `kb_entry_id`.

```
data/raw/*.csv → [bronze] lineage → [silver] entity fill + synthetic
metadata → [gold] DuckDB warehouse + CX views + Hive-partitioned Parquet
```

| Stage | Script | What it does |
|---|---|---|
| Bronze | `src/pipeline/01_bronze_ingest.py` | Lands the raw CSV as-is, adds lineage columns and a `_manifest.json` (counts, sha256). |
| Silver | `src/pipeline/02_silver_transform.py` | Decodes `flags` into `language_tags`, fills the `{{monitor name}}` placeholder, generates ticket and resolution metadata, tags `kb_entry_id`. Seeded (42). |
| Gold | `src/pipeline/03_gold_load.py` | DuckDB warehouse, 3 CX views (volume by channel, deflection by intent, handle time / CSAT by category), Hive-partitioned Parquet ready for S3 + Athena. |

```bash
python src/pipeline/01_bronze_ingest.py
```

```bash
python src/pipeline/02_silver_transform.py
```

```bash
python src/pipeline/03_gold_load.py
```

The live help desk uses the silver tables as its knowledge base
(`backend/kb_retrieval.py`).

**Evaluation track (`src/agent/`, not started beyond a skeleton):** a
LangGraph skeleton with stub nodes for offline evaluation (`python
src/agent/run_demo.py`). Still open: an 80/20 split on the 386
`kb_entry_id` values (never on raw rows, which would leak flag-variant
duplicates across the split) and a deflection / handle-time / quality
evaluation notebook.
