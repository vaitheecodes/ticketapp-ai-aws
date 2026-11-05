# ticketapp-ai

A small-scale clone of an enterprise AI customer-experience platform
(inspired by Thunai.ai), built as a portfolio project for AWS Data
Engineering / AI Engineering roles.

## Project goal

Two layers:

1. **Data layer (done)** — a bronze → silver → gold pipeline that turns a
   public LLM instruction-tuning dataset into data shaped like a real
   support ticketing platform's operational tables.
2. **AI help desk (built)** — `backend/` + `frontend/`: a Zoho-Desk-style
   help desk with an AI support chat, email-style tickets, technician
   workspace, AI incident correlation for managers, and an admin console
   (four roles, Azure OpenAI, SQLite). Built to the user's master spec
   (`HELPDESK_MASTER_SPEC.md`, kept outside the repo) via a staged plan; user-facing docs are in
   `README.md`, the API in `docs/API.md`.
   - `src/agent/` — the original stub/eval-oriented LangGraph skeleton,
     meant for offline deflection-rate evaluation on the gold dataset's
     held-out split (not wired to a real LLM; separate from the app).

## Data layer: pipeline architecture

```
data/raw/                    Site24x7_*.csv  (source of truth, untouched)
      |
      v
[01_bronze_ingest.py]        add lineage columns only, no business logic
      |
      v
data/bronze/ingest_date=YYYY-MM-DD/   raw.parquet + _manifest.json
      |
      v
[02_silver_transform.py]     the core data engineering work
      |
      v
data/silver/    tickets.parquet, resolutions.parquet (1:1, FK on ticket_id)
      |
      v
[03_gold_load.py]            load + aggregate + re-emit for the lake
      |
      v
data/gold/warehouse.duckdb   tickets, resolutions + 3 CX views
data/gold/parquet/           Hive-partitioned (ticket_month / category)
```

- `src/pipeline/config.py` — shared paths + reference data: flag legend,
  the 17-category / 27-intent enum (`CATEGORY_INTENTS`), high-risk
  categories (`HIGH_RISK_CATEGORIES`), AI-friendly vs human-leaning
  intent sets used to bias synthetic `resolved_by`.
- `src/pipeline/placeholder_fill.py` — the dataset has exactly one
  placeholder entity, `{{monitor name}}` / `{{Monitor Name}}` (case
  varies, same entity), filled from a pool of realistic Site24x7-style
  monitor names (`Prod-WebServer-01`, `API-Gateway-EU`, ...), reusing
  the same value per entity within a ticket regardless of case.
- `src/pipeline/01_bronze_ingest.py` — raw CSV in, lineage columns
  (`row_id`, `source_file`, `ingested_at`) added, `_manifest.json` emitted.
- `src/pipeline/02_silver_transform.py` — decodes flags into
  `language_tags`, fills placeholders, generates synthetic ticket
  metadata (`created_at`, `channel`, `customer_id`, `priority`) and
  resolution metadata (`resolved_by`, `resolution_status`,
  `handle_time_seconds`, `csat_score`), tags every ticket with the
  canonical KB entry it was drawn from via `kb_entry_id` (see "Dataset"
  below — this dataset's duplication is a generation artifact, not a
  repeat-contact signal, so there's no `duplicate_of_ticket_id` here).
  Seeded (42) for reproducibility.
- `src/pipeline/03_gold_load.py` — loads silver into DuckDB, builds
  `v_daily_volume_by_channel`, `v_deflection_rate_by_intent`,
  `v_handle_time_csat_by_category`, re-emits Hive-partitioned Parquet.
- `tests/test_data_quality.py` — 33 pytest checks over the pipeline output.
- `notebooks/databricks_pipeline.py` — optional PySpark port of the
  silver transform (secondary artifact, not the primary pipeline).

Full design-decision rationale (why synthetic fields are shaped the way
they are, S3/Athena extension path) lives in `README.md` — this file is
the short orientation summary, not a replacement for it.

## Schema

`tickets` (`data/gold/warehouse.duckdb`): `ticket_id` (`TCK-1NNNNN`),
`created_at`, `channel` (email/chat/voice), `customer_id`, `category`,
`intent`, `instruction_text`, `instruction_entities`, `priority`
(low/medium/high/urgent), `language_tags` (list, decoded from the
`flags` column), `kb_entry_id` (`KB-NNNN`, one of the 386 canonical
source instructions this ticket's text was drawn from).

`resolutions`: `ticket_id` (FK), `response_text`, `response_entities`,
`resolved_by` (ai/human), `resolution_status`
(resolved/escalated/pending), `handle_time_seconds`, `csat_score`
(1–5, null for pending).

17 categories / 27 intents, defined in
`src/pipeline/config.py:CATEGORY_INTENTS` (ALERTS, MONITORING,
INTEGRATION, AGENT, WEB_MONITORING, CONTAINERS, SERVER_MONITORING,
APPLICATION_MONITORING, LOGS, APM, SNMP, NETFLOW, CLOUD, ACCOUNT, DATA,
REPORTING, SUPPORT) — genuine Site24x7 support topics (monitor
setup/troubleshooting, alerting, integrations, licensing, data
retention). High-risk categories (`HIGH_RISK_CATEGORIES`, stricter
confidence bar before auto-resolving): ACCOUNT and DATA — licensing/
billing changes and data retention/deletion are the closest equivalent,
for a monitoring product, to "irreversible or consequential if wrong."

26,872 total ticket rows, but only **386 unique source instructions**
(each duplicated ~5x under different `flags` values as a
dataset-generation artifact, not independent tickets) — the real
knowledge-base size is 386 Q&A pairs, tracked per-ticket via
`kb_entry_id`. The dataset's only entity placeholder is
`{{monitor name}}` / `{{Monitor Name}}` (case varies, same entity),
filled with synthetic Site24x7-style monitor names
(`src/pipeline/placeholder_fill.py`).

## Ticket Resolution Agent — live app (`backend/`, `frontend/`)

A four-role app (customer / technician / manager / admin) on top of the
same routing policy as `src/agent/`, with real persistence, auth and LLM
calls. The bullets below are the detailed design notes per area; the
end-to-end journey is pinned by `tests/test_e2e_helpdesk.py` (real
pipeline, scripted model).

```
Customer/Agent/Admin React portals (frontend/, Vite, :5173)
        |  fetch /api/* (proxied to :8000 in dev)
        v
FastAPI backend (backend/main.py, :8000)
  /api/auth/login (all roles), /api/auth/customer/signup,
  /api/auth/{me,change-password}            JWT (backend/auth.py)
  /api/chats, /api/chats/{id}/{messages,resolve,escalate}   AI chat
  /api/chat-reviews, /api/chat-reviews/{id}   technician/manager
  /api/tickets/mine, POST /api/tickets (raise ticket), GET /api/tickets/{id} (thread),
  /{id}/{reply,reopen,attachments}            customer-facing
  /api/staff/tickets[?view=unassigned|mine|recent|open_pending]
  /api/staff/tickets/{id}, /{id}/{claim,status,messages}   technician workspace
  /api/manager/overview, /api/manager/departments          manager (read-only)
  /api/incidents[/{id}[/review|/departments|/assignee|/visibility]], /run
  /api/incidents/{id}/resolution/{draft,summary,send}      manager/assigned tech (no admin)
  /api/try-a-ticket                           technician/manager (backend/try_ticket.py)
  /api/public/incidents                       public: visibility=public history
  /api/admin/{tickets,technicians,staff,customers,
              departments,settings,audit}            admin (backend/admin.py)
  /api/meta                                   public: status labels, categories
  /api/public/routing-policy                  public, read-only (/docs); /api/debug/... alias
        |
        v
LangGraph pipeline (backend/graph.py)
  fetch_customer_history -> retrieve_kb_candidates -> classify_ticket
                                                     -> decide_route
                                                              |
                        {lookup_order -> generate_response |
                         ask_clarifying_question | human_queue}
        |
        v
SQLite (backend/ticketapp.db, auto-created, gitignored)
  customers / staff / admins / tickets / ticket_messages / attachments /
  orders / departments / audit_events / app_settings / chat_* / incident_*
  (incl. incident_deliveries, incident_memory)
```

- **KB grounding — `retrieve_kb_candidates`** (`backend/graph.py`,
  `backend/kb_retrieval.py`): runs before classification, on every
  turn. Scores the ticket text (only the customer-authored lines of a
  multi-turn transcript — the AI's own prior questions are noise once
  concatenated in) against the 386 canonical Site24x7 KB entries
  (`data/silver/{tickets,resolutions}.parquet`, deduped by
  `kb_entry_id`) via TF-IDF-weighted cosine similarity over word
  tokens, with a stopword filter tuned for this corpus — the KB's
  formally-phrased instructions ("how do I...") make generic filler
  words ("want", "team", "go") anomalously *rare*, and therefore
  anomalously high-IDF, unless stripped before scoring. The top 2-3
  candidates ground three things that used to guess independently:
  `classify_ticket`'s prompt (shown as few-shot category evidence, and
  near-duplicate entries (same category/intent, answers >= 85% the same)
  collapse into one topic so the top results are distinct (386 entries ->
  27 topics), a best match below `KB_MIN_SCORE` (default 0.35) is no match
  (the answer prompt then asks one question or offers a ticket), and
  hand-written entries in `data/kb/new_entries.json` are loaded when
  valid and `status: approved` (`backend/kb_new_entries.py`,
  `scripts/validate_kb.py`, `docs/KB_ENTRY_TEMPLATE.md`; drafts and
  `"example": true` entries never are; an entry's optional `kind`,
  `how-to` / `troubleshooting`, beats the question-wording guess in the
  how-to rule (`graph.answers_how_to`, `KBEntry.is_fault`); the
  "Getting started (how-to)" drafts are covered by
  `tests/test_kb_how_to_drafts.py`); `scripts/kb_review.py` is the
  interactive approve/edit/reject/skip reviewer (typed "yes" to approve,
  atomic save per decision; `--retag ID` changes an approved entry's
  category/terms and `--edit ID` its answer/source_url without
  re-approving (typed "yes"); new names that repeat an existing
  one, any case, and empty or match-everything regexes are refused) and records each approved entry's category
  and specific terms in `data/kb/kb_registry.json` (`backend/kb_registry.py`;
  KB-only categories never become ticket categories); an approved entry with
  `handling: escalate_with_info` that grounds a reply gets
  `chat_response.info_request` (numbered steps from its answer, never a
  question, then its `collect` list and a fixed safety note) and the chat /
  ticket goes to the support team in the same turn (`needs_info`,
  reason `kb_needs_info`);
  an entry specific to an integration or monitor type the customer
  hasn't mentioned (`backend/topic_terms.py`, by intent plus integration
  names in the answer) never grounds a reply and is passed as an
  "unconfirmed topic" to ask about (`graph.grounding_candidates`), and
  one naming another variant of its intent than the customer did (the
  Android guide for an iPad app, `topic_terms.names_another_variant`)
  is skipped; the
  category picked at chat/ticket start narrows grounding only, never
  classification or routing; prompts never ask for identifiers (the AI
  has no account access) but for monitor type, exact error, screen/step,
  what was tried, and the chat filter swaps identifier requests for one;
  a strong match — `kb_retrieval.STRONG_MATCH_SCORE` — raises its
  returned confidence past a conservative raw LLM score, logged for
  comparison); `ask_clarifying_question`'s prompt (names the *specific*
  candidate intents/example questions it's choosing between, instead of
  a free-floating meta-question about categories that don't exist);
  and `generate_response`'s prompt (the top candidate's canonical
  response as grounding, same pattern as order grounding below). No
  embedding model is wired in (see the eval-track roadmap below) — this
  is deliberately simple and dependency-free. Startup prints
  `KB: <n> entries loaded`; production refuses to start with 0;
  `/api/meta` has `kb_entries` (count only) and `scripts/aws_smoke.py`
  fails on 0; `tests/test_kb_shipped.py` loads the KB from the image's
  file set built from `git archive HEAD`. Covered by
  `tests/test_kb_retrieval.py` and `tests/test_ask_clarifying_question.py`
  (pure-function/stubbed-client tests, no live LLM calls).

- **decide_route's third branch — "clarify"** (`backend/graph.py`): a
  *non*-high-risk ticket below the base confidence bar doesn't escalate
  blind anymore. It routes to `ask_clarifying_question`, which (per KB
  grounding above) asks one targeted question naming the actual
  candidate topics rather than a generic "tell me more" — stored via
  `ai_response` so it flows through the same
  `chat_messages`/`ChatSession.jsx` path as a normal AI reply, no
  separate frontend plumbing needed. `tickets.clarification_attempts`
  (new column, default 0) tracks the count; each chat reply re-invokes
  the graph, so `classify_ticket`/`decide_route` re-run on the fuller
  transcript and can resolve to `ai` once it's no longer ambiguous.
  After `MAX_CLARIFICATION_ATTEMPTS` (3) rounds still below the bar,
  `decide_route` forces `human` and the chat escalates through the same
  `summarize_case()` path as an abusive-customer mid-chat escalation.
  **High-risk categories (ACCOUNT/DATA) below their stricter 90% bar
  never get KB-topic clarification or an AI answer**: an
  `escalate_with_info` KB entry gives its steps + collect list + safety
  note and raises the ticket; a vague first report (`clarification_attempts`
  0, not a how-to, no error reported) gets ONE question
  (`chat_response.clarify` with `high_risk`: which login, what is on the
  screen, what a vague phrase meant; no KB topics, no causes), and the
  next turn still below the bar goes to a human; anything else goes to a
  human immediately — checked *before* the clarify branch (even
  mid-clarification, discovering a ticket is actually high-risk and still
  uncertain forces an immediate human handoff). **A how-to**
  (`backend/request_kind.py`: add / set up / configure, no fault words)
  is never asked "are you referring to...?". It gets at most ONE fixed
  question (`graph.how_to_question`, no model call, checked right after
  the escalation-tag override): with no subject at all ("how to add the
  monitor") which kind of monitor (`HOW_TO_TYPE_QUESTION`), or, when the
  only guides on its subject are registered as specific to something not
  named (e.g. `agent_installation` -> Linux / Windows in
  `kb_registry.json`; `topic_terms.PLATFORMS`), which one. Once asked
  (`asked_how_to_question`, found in the transcript) it is never asked
  again, and the answer ("a website", "not sure") is still the how-to.
  Otherwise `generate_response` answers from a how-to KB entry on the
  customer's subject (`graph.on_customer_subject`: half the customer's
  words, or a strong match whose question names their main subject, so
  extra words don't hide a guide while "an SLA report for my website"
  isn't the website-check entry; reviewed `kind: how-to` guides rank
  first; troubleshooting entries never ground it, and filled-in monitor
  names like "App-Server-04" aren't indexed), or, with none, says "I
  don't have a guide for that yet" and raises the ticket
  (`NO_KB_REPLY_*`, no model call, `needs_info.reason = "kb_no_match"`,
  `graph.handoff_reason`). Flows: `tests/test_how_to_flow.py`. Steps in an answer that the top KB entry
  doesn't support are dropped (`ai_service/grounding.py`), the exact
  error message is asked for only after the customer reported an error,
  and how-to answers lose the "if you encounter issues..." line.
  Sample conversations: `tests/test_live_chat_review.py`. The escalation-language-tag hard override is checked
  before even that, and always wins. A ticket sitting in a clarify
  round has status `pending_customer` (distinct from `open`, which means
  waiting on the support team). Covered by `tests/test_decide_route.py` (pure-function
  tests, no LLM calls).

- **Order grounding — `lookup_order`** (`backend/graph.py`): before
  `generate_response` runs (ai branch only), `lookup_order` checks the
  ticket text for an explicit `ORD-XXXXX` pattern and queries the mock
  `orders` table (`backend/seed_data.py`, ~80 Faker-seeded rows,
  auto-seeded once by `db.init_db()`; a few fixed demo emails —
  `demo@example.com`/`customer@example.com`/`test@example.com` — get
  extra orders so a tester has real matches without hunting through
  seeded data). **`_ORDER_RELATED_CATEGORIES` is intentionally empty**
  — none of Site24x7's 17 categories are order/e-commerce related, so
  unlike the old Bitext-era version (which inferred "probably about an
  order" from category alone and asked every such ticket to verify
  identity), this only fires on a literal order number in the text; it
  never leaks an order-lookup/identity-verification prompt into an
  unrelated MONITORING/AGENT/ALERTS/INTEGRATION request. The result (a
  real record, a "not found", or "no order number given") is passed
  into `generate_response`'s prompt as the only source of truth for
  order facts — the prompt explicitly forbids inventing a tracking
  number, delivery date, or status not present in that context, and
  instructs an honest "I can't verify that" instead of a
  plausible-sounding guess on a miss. Covered by
  `tests/test_kb_retrieval.py`.

- **AI chat = chat sessions, separate from tickets** (`backend/chats.py`,
  `frontend/src/components/ChatSession.jsx`; reconcile sub-stage A).
  `chat_sessions` (`active | resolved | escalated | abandoned`) +
  `chat_messages` (`customer | ai`). Every customer message re-invokes
  the graph with the full transcript (after the per-message gates in
  `backend/conversation.followup_gate`, shared with ticket replies).
  **Resolved**: only on the customer's confirmation (a bare "thanks"
  after an AI answer, or "My issue is solved") — the session closes with
  an AI resolution summary and a `chat_reviews` row
  (`pending_review -> verified -> closed`, technicians/admin via
  `/api/chat-reviews`, "Chat Reviews" tab); **no ticket is created**.
  **Escalated** (customer accepts a hand-off, AI admits a limitation,
  decide_route says human, or the customer clicks "Raise a ticket"):
  creates an unassigned `open` ticket (`ticket_type = 'chat'`,
  `chat_session_id` link) carrying the AI hand-off brief
  (`ai_summary`: issue / tried / findings / unresolved questions;
  `ai_suggestions`: next steps — staff-only, never returned by customer
  endpoints; the brief text is also appended to `ticket_text` after
  `ESCALATION_MARKER` so the queue shows it). The customer is told the
  ticket reference; the transcript stays in `chat_messages` and the
  customer's ticket view shows it as a collapsible "Chat transcript".
  **AI failure** never changes chat state: the customer's message is
  kept, everything after it is rolled back (SAVEPOINT), the response
  carries `ai_error`, and "Raise a ticket" still works. Summaries come
  from `backend/ai_service/summarize_conversation.py` (validated pydantic output,
  retry once, deterministic fallback marked `source: "fallback"` — set by
  code, never the model). Pre-session chat-type ticket rows are migrated
  in place by `db._migrate_chat_tickets`. Covered by
  `tests/test_chat_sessions.py`, `tests/test_chat_followup_gates.py`,
  `tests/test_handoff_escalation.py`.
  **Bare closing acknowledgment short-circuit** (`backend/closing_ack.py`):
  a follow-up after an AI answer (chat) or on a `resolved` AI-handled
  ticket (email-style reply) isn't automatically re-classified if it's an unambiguous acknowledgment
  ("thanks", "got it", "that worked", ...) — "thanks" isn't a support
  request and used to get classified with near-zero confidence and
  wrongly escalated to a human even though the issue was solved. The
  check is deliberately conservative: any negation/continuation signal
  ("but", "still", "not"), a question mark, or more than a handful of
  words falls through to the normal pipeline instead, so "thanks, but
  it's still not working" (or any genuinely new content) still gets
  reclassified and can re-open or escalate normally. A bare
  acknowledgment resolves a chat (see above) or closes a ticket
  (`status = "closed"`, with a visible system message); further
  messages get a 400. Covered by `tests/test_closing_ack.py` (pure
  function) and `tests/test_chat_followup_gates.py` (endpoint-level,
  stubbed graph, real temp SQLite DB via FastAPI's TestClient).
  **Continuation short-circuit** (`graph.is_continuation_of_prior_response`,
  checked second, after the closing-ack check): a follow-up that refers
  back to the AI's own last reply ("what is the difference", "can you
  elaborate") is a continuation of the *same* answered thread, not a
  new ticket to triage — classify_ticket judges each message's own
  classifiability, and a short follow-up fragment has almost no
  standalone content, which used to collapse decide_route's confidence
  and wrongly escalate an on-topic question the conversation already
  makes answerable. A cheap, targeted LLM call (`prior AI reply` +
  `new message` -> `{"is_continuation": bool}`) decides this; a true
  result skips classify_ticket/decide_route entirely for that turn and
  calls `lookup_order` + `generate_response` directly, grounded via
  `retrieve_kb_candidates_for_continuation` (searches on the prior
  reply's own content, not just the uninformative new fragment — the
  same signal-to-noise problem `retrieve_kb_candidates`'s stopword
  filter solves, here solved by widening the query instead) and
  `generate_response`'s new `continuation=True` prompt note (treat the
  transcript as an ongoing exchange to build on). **The escalation-
  language hard override still wins even over a topical continuation**:
  `graph.has_escalation_language` gates the check first, so "thanks,
  but it's still not working" — topically a continuation, but carrying
  negation — is never even passed to `is_continuation_of_prior_response`
  and instead falls through to the normal pipeline, where the hard
  override escalates it as it always has. A genuinely new/different
  message (same LLM call, negative result) likewise falls through
  unchanged. Covered by `tests/test_continuation_check.py`
  (stubbed-client prompt/parsing tests) and
  `tests/test_chat_followup_gates.py` (endpoint-level).

- **Customer portal + ticket model (Stage 2)**
  (`frontend/src/pages/CustomerPortal.jsx`, `components/TicketThread.jsx`):
  two request cards — **Start a chat** (pre-chat form -> live
  `ChatSession`, see chat sessions above) and **Raise a ticket** (subject,
  category, priority, description, drag-and-drop attachments;
  `POST /api/tickets`, `ticket_type = 'ticket'`, no chat). Right panel
  has **My tickets** (tickets, incl. escalated chats with a Chat badge)
  and **Chats** (chat history, resolved ones read-only), with All/Open/Resolved/Escalated
  filters ("Escalated" = `route == 'human'`), search, unread dot
  (`tickets.customer_last_read_at`) and a Chat badge. Ticket detail is an
  email-style thread with a reply composer (`/reply` runs the same
  follow-up gates as `/chat` while the AI owns the ticket; once it's with
  the support team the reply is just appended) and Reopen on resolved
  tickets. **Statuses** (`db.TICKET_STATUSES`): `open`, `in_progress`,
  `pending_customer`, `resolved`, `closed` (legacy `awaiting_reply` /
  `assigned` are migrated in place). For `ticket_type = 'ticket'`,
  `generate_response`/`ask_clarifying_question` get
  `reply_format='email'`: the LLM writes only the body and
  `email_utils.format_email_reply` adds "Hi <first name>," + closing +
  "Regards, Site24x7 Support Team"; AI replies are also emailed via
  `email_utils.send_email` when SMTP is configured (silent no-op
  otherwise, sent as a BackgroundTask). A pipeline failure on a new ticket
  still creates it, queued for a human. **Attachments**
  (`POST /api/tickets/{id}/attachments`, `attachments` table): max 5 per
  message, 10 MB each, png/jpg/pdf/txt/log with the contents checked
  against the extension, stored under `db.uploads_dir()` (DB_PATH's
  parent `/uploads`) with random names. Covered by
  `tests/test_customer_tickets.py`.

- **Admin area: users, dashboard, SLA** (`backend/admin_users.py`,
  `backend/admin_reports.py`, `backend/sla.py`; `UsersPanel`,
  `DashboardPanel`, `SlaPanel`, `OneTimePassword`, `ui/SlaChip`). Every
  endpoint admin-only on the server. **Users**: one list of customers,
  technicians and managers (`/api/admin/users`, same functions as the
  older `/api/admin/staff` and `/api/admin/customers`); never the admin
  (env-seeded) and never role `admin`. Create and reset generate the
  temporary password server-side (`auth.generate_temporary_password`),
  returned once with `Cache-Control: no-store`, stored only hashed, never
  in a list or the audit log; forced change at first sign-in.
  Deactivated users can't sign in, tickets kept. **Dashboard**
  (`/api/admin/dashboard`): counts by status/priority, created/resolved
  per day, open by priority and technician, average first-response and
  resolution time per priority, date range + priority filters; aggregated
  in Python so it runs the same on SQLite and Postgres. **SLA** (settings
  section `sla`, off by default): per priority a first-response and a
  resolution target; a ticket gets targets and due times at creation
  (`ticket_lifecycle.record_created` -> `sla.on_created`), a priority
  change recalculates from creation (`set_priority`); first response =
  first public reply by a staff member (or the AI on an AI-handled
  ticket), via `conversation.insert_ticket_message`; the resolution clock
  pauses in Pending Customer and Resolved/Closed (`set_status` ->
  `sla.on_status_changed`). Calendar 24x7 only; `sla.add_working_time` /
  `working_seconds` are where business hours go. States on_track /
  at_risk (`at_risk_percent`, default 75) / breached / met / paused;
  `/api/admin/sla` gives compliance, totals and the near-breach list;
  `sla` is on admin and staff ticket payloads, never customer ones. New
  ticket columns (`first_response_at`, `resolved_at`, `sla_*`):
  `db._ADDED_COLUMNS` + backfill for SQLite,
  `migrations/postgres/0002_sla.sql` for Postgres. Covered by
  `tests/test_admin_area.py` (run on Postgres with
  `DB_BACKEND=postgres`).
- **Admin portal** (`frontend/src/pages/AdminPortal.jsx`): the single
  admin (see Auth). Tabs: **Dashboard**, **All Tickets** (`GET /api/admin/tickets`,
  every field, every customer — including a **Technician** column with a live dropdown to
  (re)assign or unassign any human-routed ticket via
  `POST /api/admin/tickets/{id}/reassign`; picking "Unassigned" sends it
  back to the open queue; a **Priority** dropdown and the **SLA** state),
  **SLA**, **Users** (see the admin-area bullet above), **Departments**
  (`components/DepartmentsPanel.jsx`: create/edit/delete
  departments and their KB-category mapping — each category belongs to
  at most one department, enforced server-side), **Settings** and
  **Activity**. Each section is its own route (`/admin/<section>`). Chat
  Reviews (technicians + managers), Incidents (managers), **Try a ticket**
  (`/try-a-ticket`, `POST /api/try-a-ticket`, technicians + managers: the
  live pipeline trace without saving) and the **routing policy** (public,
  read-only at `/docs/routing-policy`) are not in the admin console, and
  the admin gets 403 on their endpoints (`tests/test_role_scope.py`).
  Reassignment is restricted to `route == 'human'` tickets — an
  ai/clarify ticket was never queued for a human, so there's nothing
  to hand off.

- **Frontend design system** (`frontend/src/styles/theme.css`,
  `src/ui/` shadcn-pattern components on Radix, `src/layout/AppShell.jsx`,
  `PRODUCT.md`): Tailwind v4, Lucide icons, Recharts, self-hosted IBM Plex
  Sans/Mono. "Framed sheet" layout: one tinted frame holds a grouped
  sidebar (per role, `layout/nav.js`) and a 48px top bar (page title,
  theme menu System/Light/Dark, user menu); the page sits on a white sheet
  with a 1px edge. Under 768px the sidebar becomes a drawer. Colour only on
  data, status and the one primary action; every text token >= 4.5:1 in
  both themes. Public pages (`/docs`, `/incidents`) use `PublicShell`.
  The UI names the product "Help Desk" and the bot "Support Bot" (no
  third-party brand in user-visible text). The portal shown is picked
  from the role inside the token (`lib/tokenRole.js`, `api.getRole`), never
  a separately stored role, and a sign-in/out in another tab reloads the
  others (`api.onSessionChange`): a stale stored role once showed the admin
  the technician workspace, all 403s (`tests/test_frontend_session.py`).
  Pages not yet rebuilt still use
  the old stylesheets, imported into a lower cascade layer
  (`src/styles/legacy/`) until they are converted.

- **Routing policy** (`backend/graph.py`) starts from the same
  thresholds as `src/agent/state.py` + `src/agent/nodes/decide_route.py`
  (base confidence 75%, high-risk categories ACCOUNT/DATA need ≥90%),
  but where the original always escalated below that bar, the
  live app's `decide_route` now tries `clarify` first for non-high-risk
  categories (see above) — high-risk categories below their bar still
  escalate immediately, and `offensive_language`/`negation`/
  `coordinated_syntax` language tags force immediate escalation
  regardless of confidence, category, or clarification state. Since real
  tickets arrive with no pre-decoded flags (unlike the gold dataset),
  `classify_ticket` runs a small regex heuristic to populate
  `language_tags` itself. `/api/public/routing-policy`'s `decision_order`
  (shown live at `/docs/routing-policy`, public; `/debug` redirects there)
  is the source of truth for the exact precedence — read from
  `graph.py`'s constants, not hardcoded in the frontend.
- **Agent-facing ticket text** (`frontend/src/utils/parseTicketText.js`,
  `TicketTextDetail.jsx`): `ticket_text` sometimes has a chat case
  summary appended (the `--- Escalated after chat ---` marker
  `main.py`'s chat handler writes). The Queue/My Tickets list preview
  shows the case summary when present (more useful for scanning than the
  start of the raw concatenated string), and expanding a row (click the
  row, chevron toggles) renders "Customer's original message" and "AI
  summary" as distinct labeled sections instead of one run-on string.
  Tickets that escalated without a chat (e.g. an immediate high-risk
  escalation) have no marker, so `summary` comes back `null` and only
  the original-message section renders — no empty summary block. Same
  parsing reused in the admin All Tickets table's Text column.
- **AI service** (`backend/ai_service/`, reconcile sub-stage C): every
  model call goes through it — one module per operation
  (`categorize_ticket`, `chat_response` [answer / clarify /
  is_continuation], `summarize_conversation`, `summarize_ticket`,
  `suggest_steps`, `correlate_incidents`, `summarize_incident`,
  `draft_post_incident`, `incident_question`), each
  with pydantic input/output schemas in `schemas.py`. `provider.py` is
  the only entry point (`complete_json` / `complete_text` -> `complete`
  -> the backend chosen by `AI_PROVIDER`, `backends.py`: `quickml_appsail`
  [Catalyst QuickML LLM Serving REST call, one stateless prompt string,
  authenticated with the admin credential AppSail injects into each
  request — captured per request by `main._CatalystRequestContext` into
  the `backends.appsail_credentials` contextvar (`AppSailCredentials`,
  secrets hidden from repr), never stored globally or logged; exact
  request in `docs/quickml-appsail-call.md`; response text at
  `data[0].data`; no SDK `ask_llm` exists and the Zoho self client is
  blocked, so this only works on AppSail], `openai_compatible`, `azure`
  [until Phase 4 C6], `mock` [default when unset; minimal schema-valid
  JSON; the only option locally]).
  Instructions in the system prompt, untrusted text in delimited data
  blocks (closing tags escaped); single-prompt backends flatten to
  Instructions / Data / Correction / Response sections. Replies are
  cleaned (`<think>` reasoning and code fences stripped, the JSON object
  extracted; a plain-text reply wrapped as `{"response": ...}` unwrapped),
  validated (+ semantic `check`, e.g. correlation ids must be
  from the input), retried once with a note giving the validation error
  paths (never the rejected values), then `AIError`. Provider errors are
  never retried, except Bedrock throttling/timeouts (one retry after ~1 s
  plus jitter, in `BedrockBackend.generate`); QuickML codes are mapped (`INVALID_ENDPOINT_KEY` — an
  HTTP 500 — and `ORGID_HEADER_UNAVAILABLE` are `ConfigError`s logged at
  ERROR). No embeddings with
  QuickML: incident grouping uses lexical TF-IDF (`incidents._lexical_vectors`,
  `INCIDENT_SIMILARITY` default 0.15). `scripts/ai_smoke_test.py` runs the
  key prompts against the real provider. Provider errors log the
  exception type only and are re-raised without the chained cause.
  `graph.py` keeps routing policy + KB grounding and calls the service;
  callers treat `AIError` as "AI unavailable" without changing state.
  Hand-off briefs (`conversation.handoff_brief`) = summary (tidied by
  `summarize_conversation.tidy_handoff`: no "N/A" items, "Already tried:
  Nothing stated yet", no findings that restate the issue) + next steps
  only from KB entries matching the customer's own words
  (`graph.handoff_kb_candidates`; model steps the entry doesn't support
  dropped, else the entry's sentences; "No KB match - needs triage"
  without a match), each with its own fallback. `.env` is loaded once in
  `backend/__init__.py` (before it was loaded after `auth` read
  `JWT_SECRET`, so local dev silently used the default secret). Tests
  can't reach the provider (`tests/conftest.py`); covered by
  `tests/test_ai_service.py`.
- **AI incident correlation** (reconcile sub-stage D; `backend/incidents.py`,
  `frontend/src/components/IncidentsPanel.jsx`). After every new ticket
  or chat escalation (BackgroundTask) and on "Check now"
  (`POST /api/incidents/run`): tickets from the last
  `INCIDENT_WINDOW_HOURS` (not in a dismissed cluster) are grouped by
  component area — the department owning their category (the classifier
  often splits one outage across AGENT/SERVER_MONITORING), else the
  category — optionally split by embedding similarity
  (`AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `INCIDENT_SIMILARITY`). Groups
  with `INCIDENT_MIN_TICKETS`+ tickets from 2+ customers go to
  `ai_service.correlate_incidents`; groups already fully inside an open
  cluster aren't re-sent. Related tickets join the overlapping open
  cluster or a new one (`incident_clusters`, `incident_tickets`); tickets
  are only linked, never changed. Departments: owners of the tickets'
  categories + the AI's (normalised) suggestions, only at confidence >=
  `INCIDENT_ROUTING_CONFIDENCE`, else `needs_routing`. Manager/admin
  corrections (`source = 'manager'`) survive AI re-runs. Review:
  confirm / investigate / dismiss with a note -> `incident_reviews`.
  Managers see clusters linked to their departments plus unrouted ones,
  and may open any linked ticket. Customers on a confirmed cluster's
  tickets get a neutral `incident_notice`. No reply-to-all fan-out.
  Covered by `tests/test_incidents.py`.
- **Post-incident summary + incident memory** (`backend/incident_resolution.py`,
  `backend/incident_memory.py`, `backend/timeutil.py`,
  `ai_service/draft_post_incident.py`, `ai_service/incident_question.py`,
  `frontend/src/components/IncidentResolution.jsx`,
  `pages/IncidentHistory.jsx`). Incident statuses gain `resolved`;
  new cluster fields `started_at` (earliest linked ticket), `resolved_at`
  (UTC, shown in IST), `resolution_note`, `root_cause`,
  `customer_summary`, `internal_summary`, `summary_status`
  (none/draft/approved/sent), `visibility` (affected_only/public),
  `assigned_technician_id`, `resolved_by`. Only a **confirmed** incident
  can be resolved, by a manager who can see it, the admin, or the
  technician it's assigned to (`PUT /api/incidents/{id}/assignee`;
  technicians see only assigned incidents). The draft op gets the
  incident's facts + the staff note only; code composes the When line
  and the Cause line (staff root cause verbatim or "still being
  reviewed"), and `_check` rejects model text with times/dates/relative
  days or cause claims. Approve & send never runs automatically and must
  echo the saved text; each linked ticket is delivered in its own
  transaction with an `incident_deliveries` row (sent tickets skipped,
  failures recorded, re-send retries failures only), posted as a public
  `staff` message with no author (shown as "Site24x7 Support Team",
  `incident_update` flag in the customer thread); real email only via
  `Outbox` when `EMAIL_ENABLED`. Optional mark-Resolved via
  `ticket_lifecycle`; never close/merge. `incident_memory.remember()`
  snapshots confirmed/resolved incidents (forgets dismissed) for
  `INCIDENT_MEMORY_DAYS` (`app_settings` incidents.memory_days).
  `answer_incident_question` runs first in `chats._respond` and
  `main._run_followup` (keyword gate -> AI parses an IST time window ->
  `find_matches` only over memories the customer is linked to or that are
  public -> fixed templates + stored summary; the model never writes the
  answer). `GET /api/public/incidents` (no auth) lists public ones.
  Covered by `tests/test_incident_resolution.py`.
- **Admin console, settings, audit** (reconcile sub-stage E;
  `backend/admin.py`, `backend/app_settings.py`, `backend/audit.py`).
  All admin endpoints live in `admin.py` (admin role on every one):
  tickets + reassign + simulator, Team (`PATCH /api/admin/staff/{id}`:
  name/email/role/department; a technician with open tickets can't change
  role; a role change invalidates their token), Customers (create with a
  forced password change, edit, deactivate — live sessions blocked —
  reset password), departments, settings, audit log. **A manager belongs
  to one department** (`staff.department_id`; the legacy
  `manager_departments` table is read once by the migration — first
  mapping kept — and no longer written); technicians have none.
  **Settings** (`app_settings`): `routing` (confidence bars, high-risk
  categories, escalation language tags, max clarifying rounds — read by
  `graph.decide_route` on every decision), `incidents` (window, min
  tickets, similarity, routing confidence — admin value > `INCIDENT_*`
  env > default), `statuses` (display labels only; the five statuses are
  fixed; served by public `GET /api/meta` and loaded by `main.jsx` before
  render). Cached in memory per process (loaded at startup, refreshed on
  save). **Audit log** (`audit_events`): ticket status/assignment changes
  and claims (via `ticket_lifecycle`), incident reviews and department
  corrections, chat review updates, staff/customer/department/settings
  admin changes, password changes and failed logins — ids, statuses and
  field names only, never bodies or passwords; filterable at
  `GET /api/admin/audit` (Activity tab). **AI failures**
  (`backend/ai_failures.py`): every failure of a chat turn, ticket reply
  or new ticket is audited as `chat.ai_failure` / `ticket.ai_failure`
  with kind (throttling, timeout, access_denied, validation, parse_schema,
  filter_rejected, config, unknown), error code name, attempts, latency,
  operation and outcome (`failed` = the customer saw "AI unavailable";
  `regenerated`/`fallback` = our own check rejected a reply and the chat
  recovered with a regenerated reply or a fallback question); never
  provider text or customer text; admin view `GET /api/admin/chats/{id}`.
  Chat replies also get a repeat guard (>= 80% word overlap with one of
  the last two AI replies: regenerate once, then a different question or
  a ticket offer), a vague-message open question (no symptom yet: no KB
  grounding, no model call), and a latest-message-first KB query
  (`graph.retrieval_query`). **Real email is off** unless
  `EMAIL_ENABLED=true` + SMTP (`Outbox.flush` / `email_utils.email_enabled`).
  Attachments are only served by the authorised download endpoint (no
  static mount). Covered by `tests/test_admin_console.py`.
- **Auth / roles** (`backend/auth.py`): customer, technician, manager,
  admin. Tables: `customers`, `staff` (technicians + managers, `role`
  column, `active`, `must_change_password`), `admins`. One login page /
  `POST /api/auth/login` for every role; the token carries the role and
  the frontend routes to `/customer`, `/technician`, `/manager` or
  `/admin`. Only customers can sign up publicly; staff are created by the
  admin with a temporary password and must change it on first login
  (`require_role` blocks every other endpoint until they do). Exactly one
  admin, created on startup from `ADMIN_EMAIL`/`ADMIN_PASSWORD`
  (`auth.ensure_admin`, never overwrites an existing admin); outside
  production a dev fallback is used with a loud warning, and
  `APP_ENV=production` refuses to start without them. Staff/admin
  dependencies re-read the DB per request, so deactivation takes effect
  immediately. Emails are unique across all three tables. The legacy
  `agents` table / `assigned_agent_id` column are migrated in place to
  `staff` / `assigned_technician_id` by `db.init_db()`. Dev-only demo
  manager + technician are seeded by `seed_data.seed_demo_staff`.
  bcrypt-hashed passwords, JWT bearer tokens (`JWT_SECRET`). Covered by
  `tests/test_roles_auth.py`.
- **Ticket lifecycle + technician workspace** (reconcile sub-stage B;
  `backend/staff_tickets.py`, `backend/ticket_lifecycle.py`,
  `frontend/src/components/StaffTicketView.jsx`). Statuses
  `db.TICKET_STATUSES`: Open (with the support team, owner optional), In
  Progress, Pending Customer, Resolved, Closed. Every status/owner change
  goes through `ticket_lifecycle.set_status` / `set_assignee` /
  `record_assignment`, which write `ticket_status_history` /
  `ticket_assignment_history` (actor = user id, or `system` for the AI
  pipeline). **Claim** (`POST /api/staff/tickets/{id}/claim`):
  `BEGIN IMMEDIATE` + `UPDATE ... WHERE assigned_technician_id IS NULL`,
  so of two simultaneous claims one gets 409. Only the owner replies
  publicly or changes status; any technician adds internal notes;
  managers are read-only (their departments' categories only, plus
  `/api/manager/overview` workload); admin reassign is logged.
  **Message visibility** (`ticket_messages.visibility`): `public_reply`,
  `internal_note`, `system_event` (+ `customer_visible`). Customer
  endpoints filter with `db.CUSTOMER_VISIBLE_SQL` in the query, and the
  AI transcript (`main._transcript_for`) only ever includes public
  replies. Customer reply on Pending Customer / Resolved -> Open (owner
  kept). AI-handled tickets (route ai/clarify) stay out of the workspace.
  Covered by `tests/test_ticket_workspace.py`.
- **Rate limiting + production secrets** (`backend/rate_limit.py`,
  `auth.check_production_config`, `db_postgres.production_problems`):
  `/api/auth/login` is limited per client IP (every attempt) and per
  account (failures only; success clears them), 429 + Retry-After, checked
  before the password; sign-up per IP (`check_signup`, 5/hour); every AI
  endpoint (start chat, chat message, new ticket, ticket reply) through
  `auth.get_ai_customer` -> `check_ai_turn`: 20 turns / 10 min and 200 /
  day per customer, 60 / 600 per IP, refused before the model runs. Caps:
  ticket description 8000, reply 4000, sign-up name 120 / email 254,
  passwords at most 72 bytes (bcrypt's limit; 400 on sign-up/change,
  `verify_password` compares the first 72 bytes so a long login never
  500s), and `conversation.latest_transcript` sends the model at most the
  latest 12,000 characters of a conversation. Env-configurable, in-memory
  per process; client IP from X-Forwarded-For only with
  `TRUSTED_PROXY_HOPS` (1 behind an ALB). Off in tests by default
  (`tests/conftest.py`; `tests/test_ai_limits.py` turns them on). In production startup
  refuses a placeholder/short JWT_SECRET, a weak ADMIN_PASSWORD, and on
  Postgres a weak/local-only DB password or no required SSL. Covered by
  `tests/test_rate_limit.py`, `tests/test_roles_auth.py`.
- **Container / AWS hosting** (`Dockerfile`, `.dockerignore`,
  `docs/AWS_DEPLOY.md`): python:3.13-slim, runtime requirements only,
  non-root, `/health` check; planned on ECS Express Mode (Fargate) + RDS +
  Amplify, not deployed (`AWS_PLAN.md`). `tests/test_dockerfile.py`.
  Demo-only hosting: `scripts/aws_up.sh` (idempotent; restores RDS from
  the newest `ticketapp-db-*` snapshot, creates/updates the Express
  service, rebuilds the Amplify frontend only when the API URL changed,
  runs `scripts/aws_smoke.py`) and `scripts/aws_down.sh` (typed "yes",
  snapshot first, then deletes the service and DB, keeps the newest
  `KEEP_SNAPSHOTS`). Without Docker, `scripts/aws_build_image.sh`
  builds the image in CodeBuild (clean git tree required, tag = short
  commit hash, exits early if ECR has it; x86_64 only, so `aws_up.sh`
  asks for ARM64 only when the manifest has an arm64 variant). Shared
  helpers (incl. `ensure_ecr_repo`) in `scripts/aws_lib.sh`; tested
  against a fake `aws` CLI (`tests/fake_aws_cli.py`,
  `tests/test_aws_scripts.py`) that rejects options the real subcommand
  doesn't accept (`tests/fake_aws_options.json`, generated from the CLI's
  help by `tests/aws_cli_options.py`).
- **Run locally**: `uvicorn backend.main:app --reload` (repo root, needs
  `.env`) + `npm run dev` in `frontend/` (proxies `/api` to `:8000`).
- **Phase 2 (not yet done)**: swap SQLite → Azure SQL/Cosmos in
  `backend/db.py`, plain JWT → Azure AD B2C in `backend/auth.py`,
  `uvicorn --reload` → Azure App Service/Functions. No pipeline-logic
  changes expected.

## Status

**Done:** full bronze/silver/gold pipeline, DuckDB warehouse + CX views,
data-quality test suite, exploratory notebook. The AI help desk
(`backend/` + `frontend/`): roles and auth, AI chat sessions with
resolution/escalation, email-style tickets with attachments, technician
workspace (claim, public replies, internal notes, history), modular
validated AI service, AI incident correlation with manager review, admin
console (accounts, departments, settings, audit log). Covered by the
pytest suite including an end-to-end journey test; each portal was also
checked in a browser against the live Azure OpenAI deployment. Known
limitations are listed in `README.md`.

**Next — `src/agent/` eval track** (separate from the live app above,
still open):

1. ✅ LangGraph skeleton with stub nodes (`src/agent/`) — routing logic
   on dummy data, no Bedrock calls yet.
2. ⬜ KB/held-out split — split the **386 unique `kb_entry_id` values**
   80/20 first, then map tickets back to a `split` column, so
   retrieval never sees a test ticket's own gold response. Must split
   on `kb_entry_id`, not raw ticket rows: since ~98% of rows are one of
   386 canonical instructions duplicated ~5x under different `flags`
   values, splitting on rows would put near-duplicate flag-variants of
   the same question on both sides of the split and make the eval
   meaningless.
3. ⬜ Wire in real Bedrock Claude calls for classification and
   generation (RAG grounded in retrieved KB entries) — or retire this
   track in favor of the live app's Azure OpenAI pipeline above.
4. ⬜ `notebooks/eval.ipynb` — run the held-out set through the agent,
   report deflection rate, simulated handle time, and an LLM-judge
   quality score (logged, not gating).

Graph shape (`src/agent/`): `ingest_ticket → classify_intent →
retrieve_knowledge → decide_route → {generate_response | escalate} →
log_outcome`. Same routing policy as the live app's `decide_route`.
