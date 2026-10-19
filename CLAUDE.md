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
  /api/chat-reviews, /api/chat-reviews/{id}   technician/admin
  /api/tickets/mine, POST /api/tickets (raise ticket), GET /api/tickets/{id} (thread),
  /{id}/{reply,reopen,attachments}            customer-facing
  /api/staff/tickets[?view=unassigned|mine|recent|open_pending]
  /api/staff/tickets/{id}, /{id}/{claim,status,messages}   technician workspace
  /api/manager/overview, /api/manager/departments          manager (read-only)
  /api/incidents[/{id}[/review|/departments|/assignee|/visibility]], /run
  /api/incidents/{id}/resolution/{draft,summary,send}      manager/admin/assigned tech
  /api/public/incidents                       public: visibility=public history
  /api/admin/{tickets,technicians,simulate-ticket,staff,customers,
              departments,settings,audit}            admin (backend/admin.py)
  /api/meta                                   public: status labels, categories
  /api/debug/routing-policy                   dev-only, unauthenticated
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
  a strong match — `kb_retrieval.STRONG_MATCH_SCORE` — raises its
  returned confidence past a conservative raw LLM score, logged for
  comparison); `ask_clarifying_question`'s prompt (names the *specific*
  candidate intents/example questions it's choosing between, instead of
  a free-floating meta-question about categories that don't exist);
  and `generate_response`'s prompt (the top candidate's canonical
  response as grounding, same pattern as order grounding below). No
  embedding model is wired in (see the eval-track roadmap below) — this
  is deliberately simple and dependency-free. Covered by
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
  skip clarify entirely and escalate to human immediately** —
  licensing/billing or data-retention ambiguity is exactly the case
  where a human should be involved right away, not probed by the AI
  first — checked *before* the clarify branch and unconditional on
  `clarification_attempts` (even mid-clarification, discovering a
  ticket is actually high-risk and still uncertain forces an immediate
  human handoff). The escalation-language-tag hard override is checked
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

- **Admin portal** (`frontend/src/pages/AdminPortal.jsx`): the single
  admin (see Auth) — four tabs: **All Tickets** (`GET /api/admin/tickets`,
  every field, every customer — including a **Technician** column with a live dropdown to
  (re)assign or unassign any human-routed ticket via
  `POST /api/admin/tickets/{id}/reassign`; picking "Unassigned" sends it
  back to the open queue), **Team** (`components/TeamPanel.jsx`: create
  technicians/managers with a temporary password, deactivate/reactivate,
  reset password, edit role and the manager's one department; create/edit/delete
  departments and their KB-category mapping — each category belongs to
  at most one department, enforced server-side), **Try a Ticket**
  (`POST /api/admin/simulate-ticket` — runs a ticket through the live
  pipeline with real Azure OpenAI calls without persisting it, and shows
  category/confidence/route/route_reason/language_tags; this is the
  per-ticket pipeline-trace view that `src/ui/app.py`'s Streamlit demo
  used to show), and **Routing Policy** (same live-from-`graph.py` panel
  as the standalone `/debug` route, via the shared `RoutingPolicyPanel`
  component). Reassignment is restricted to `route == 'human'` tickets —
  an ai/clarify ticket was never queued for a human, so there's nothing
  to hand off.

- **Frontend design system** (`frontend/src/index.css`,
  `components/Icons.jsx`): Fira Sans (Google Fonts, loaded in
  `index.html`) — chosen over Inter/Roboto/the usual defaults, which
  read as generic in AI-generated UIs. Real hand-authored SVG icons
  (`Icons.jsx`) instead of emoji/unicode glyphs — every icon there is
  decorative (`aria-hidden`) since a visible text label always sits next
  to it. `--color-text-muted` is `#5b6472` in light mode specifically to
  clear 4.5:1 contrast against `--color-bg` (the original `#6b7280` sat
  at ~4.43:1, just under WCAG AA). Every form field has a real
  `<label htmlFor>` (via `.field-group`/`.field-label`), not a
  placeholder-only input. Disclosure buttons (ticket row
  expand/collapse) carry `aria-expanded`/`aria-controls`. Verified with
  `.agents/skills/impeccable/scripts/impeccable detect` (zero findings)
  across the whole frontend, and manually across light/dark ×
  375/768/1440px.

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
  `language_tags` itself. `/api/debug/routing-policy`'s `decision_order`
  (shown live in `RoutingPolicyPanel`, both at `/debug` and in the admin
  portal) is the source of truth for the exact precedence — read from
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
  the only place that talks to Azure OpenAI (lazy `AzureOpenAI` client;
  config from env: `AZURE_OPENAI_ENDPOINT` / `_API_KEY` / `_API_VERSION`
  / `_DEPLOYMENT`): instructions in the system message, untrusted text in
  delimited data blocks (closing tags escaped), structured output
  validated (+ semantic `check`, e.g. correlation ids must be from the
  input) with one retry, then `AIError`. Provider errors log the
  exception type only and are re-raised without the chained cause.
  `graph.py` keeps routing policy + KB grounding and calls the service;
  callers treat `AIError` as "AI unavailable" without changing state.
  Hand-off briefs (`conversation.handoff_brief`) = summary + KB-grounded
  `suggest_steps`, each with its own fallback. `.env` is loaded once in
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
  `GET /api/admin/audit` (Activity tab). **Real email is off** unless
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
