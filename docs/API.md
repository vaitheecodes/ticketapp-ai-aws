# Help desk API reference

FastAPI backend (`backend/`). All paths are under `/api`. JSON in and out
unless noted. Interactive docs with full schemas: `GET /docs` (Swagger)
on a running server.

**Auth.** `POST /api/auth/login` returns a JWT; send it as
`Authorization: Bearer <token>`. Every protected endpoint checks the role
on the server and re-reads the account on each request, so deactivation
and forced password changes apply immediately. Errors use FastAPI's
`{"detail": "..."}` shape with these statuses: `401` not signed in,
`403` wrong role / not yours / password change required / deactivated,
`404` not found, `409` conflict (e.g. a ticket already claimed), `422`
invalid input (also over a size cap: ticket description 8000, reply 4000,
sign-up name 120 / email 254 characters), `429` rate limited with
`Retry-After` (sign-in; sign-up, 5 per hour per IP; and the AI endpoints,
`POST /api/chats`, `/api/chats/{id}/messages`, `POST /api/tickets` and
`/api/tickets/{id}/reply`, which together allow 20 turns per 10 minutes
and 200 per day per customer, more per IP), `503` AI provider unavailable
(simulator only), or, on any
endpoint, the database is busy or unreachable (`Retry-After: 2`; with
`DB_BACKEND=postgres`, when no pooled connection frees up within
`DB_POOL_TIMEOUT`).

Roles: **C** customer · **T** technician · **M** manager · **A** admin ·
**public** no token.

---

## Authentication

| Method & path | Role | Request | Response |
|---|---|---|---|
| `POST /auth/customer/signup` | public | `{name, email, password}` (password ≥ 8) | `{user_id, role:"customer", name, must_change_password:false, token}` |
| `POST /auth/login` | public | `{email, password}` | `{user_id, role, name, must_change_password, token}`. 401 bad credentials, 403 deactivated. Failures are audited. |
| `GET /auth/me` | any | — | `{user_id, role, name, email, must_change_password}` |
| `POST /auth/change-password` | any | `{current_password, new_password}` | `{ok:true}`; clears `must_change_password` |

There is no signup for staff or the admin: the admin is created from
`ADMIN_EMAIL`/`ADMIN_PASSWORD` at startup, staff by the admin.

## Public metadata

| Method & path | Role | Response |
|---|---|---|
| `GET /meta` | public | `{status_labels:{open, in_progress, pending_customer, resolved, closed}, categories:[...], kb_entries}` (`kb_entries`: how many KB entries loaded, a count only) |
| `GET /categories` | public | `["ACCOUNT", "AGENT", ...]` (the 17 KB categories) |
| `GET /public/routing-policy` | public, read-only | live routing policy: thresholds, high-risk categories, escalation tags, max clarifying rounds, decision order (the `/docs/routing-policy` page). `GET /debug/routing-policy` is the older path, same response. |

## AI chat (customer)

A chat is separate from tickets until it escalates. Every response is the
whole session view:
`{session_id, status:"active"|"resolved"|"escalated"|"abandoned", issue, category, created_at, updated_at, resolved_at, ticket_id, messages:[{message_id, role:"customer"|"ai", content, created_at}], ai_error}`.
`ai_error` is a message for the customer when the AI was unavailable: their
message is kept and nothing else changed.

| Method & path | Role | Request | Notes |
|---|---|---|---|
| `POST /chats` | C | `{issue, category?, contact_name?}` | Starts the chat; the AI answers, asks a clarifying question, or escalates. |
| `GET /chats` | C | — | `[{session_id, status, issue, created_at, updated_at, resolved_at, ticket_id}]` newest first |
| `GET /chats/{id}` | C (own) | — | session view |
| `POST /chats/{id}/messages` | C (own) | `{message}` | active chats only (400 otherwise) |
| `POST /chats/{id}/resolve` | C (own) | — | "My issue is solved": resolved + summary + internal review record; no ticket |
| `POST /chats/{id}/escalate` | C (own) | `{subject?}` | "Raise a ticket": creates an unassigned Open ticket with the AI hand-off; works when the AI is down |

## Tickets (customer)

| Method & path | Role | Request | Response / notes |
|---|---|---|---|
| `POST /tickets` | C | `{subject, description, priority:"low"\|"medium"\|"high"\|"urgent", category?}` | `{ticket_id, message_id, ticket_type:"ticket", route, status, ai_response}`. The AI may answer by email-style reply, ask a question (`pending_customer`) or hand off (`open`). If the AI fails, the ticket is still created, Open. |
| `GET /tickets/mine` | C | — | `[{ticket_id, ticket_type, subject, status, priority, category, created_at, last_updated, unread, escalated}]` |
| `GET /tickets/{id}` | C (own) | — | `{ticket:{ticket_id, ticket_type, subject, status, route, priority, category, created_at}, messages:[{message_id, sender, sender_name, sender_role, to, subject, message, created_at, attachments}], chat:{session_id, transcript}\|null, incident_notice:string\|null}`. Marks replies read. Only public replies and customer-visible events — internal notes, AI summaries and suggestions are filtered out in the query. |
| `POST /tickets/{id}/reply` | C (own) | `{message}` | `{route, status, message_id, ai_error?}`. Pending Customer / Resolved → Open. 400 if closed. |
| `POST /tickets/{id}/reopen` | C (own) | — | `{ticket_id, status}`; resolved tickets only |
| `POST /tickets/{id}/attachments` | C (own) | multipart: `message_id`, `files` (≤5 per message, ≤10 MB, png/jpg/pdf/txt/log, contents must match) | `[{attachment_id, filename, content_type, size_bytes}]`. A request declaring more than 55 MB gets `413`, one with no `Content-Length` `411`, before the body is read. |
| `GET /tickets/{id}/attachments/{attachment_id}` | C (own, visible messages) / T, M (tickets they may open in the workspace: with the support team; a manager's departments only) / A | — | file download (`Content-Disposition: attachment`) |

## Technician workspace

| Method & path | Role | Request | Response / notes |
|---|---|---|---|
| `GET /staff/tickets?view=&q=&priority=&status=` | T | `view`: `unassigned` (oldest first) \| `mine` \| `recent` \| `open_pending` | `[{ticket_id, subject, status, priority, ticket_type, category, created_at, last_activity, customer_name, customer_email, assigned_technician_id, assignee_name, preview}]` (tickets with the support team only) |
| `GET /staff/tickets/{id}` | T, M (own department or linked incident), A | — | `{ticket:{..., description, customer, assignee}, messages:[... visibility, customer_visible, author ...], ai:{summary, suggestions, case_summary}, chat, incidents, status_history, assignment_history, can:{claim, reply, change_status, note}}` |
| `POST /staff/tickets/{id}/claim` | T | — | workspace. 409 if someone else claimed it first (atomic). |
| `POST /staff/tickets/{id}/status` | T (owner) | `{status}` | workspace; adds a customer-visible status event |
| `POST /staff/tickets/{id}/messages` | T | `{body, visibility:"public_reply"\|"internal_note", subject?, set_status?}` | workspace. Public replies: owner only. Notes: any technician; never shown to customers. |
| `GET /chat-reviews?status=` | T, M | — | AI-resolved chats with summary + transcript |
| `POST /chat-reviews/{id}` | T, M | `{status:"verified"\|"closed"}` | `pending_review → verified → closed` |

## Manager

| Method & path | Role | Response / notes |
|---|---|---|
| `GET /manager/departments` | M | `[{department_id, name, categories, manager_ids}]` (zero or one) |
| `GET /manager/overview` | M | `{categories, tickets:[...], workload:[{staff_id, name, open, in_progress, pending_customer, resolved}], unassigned}` for the manager's department. Read-only. |

## Incidents (managers; technicians for incidents assigned to them; the admin gets 403)

| Method & path | Request | Response / notes |
|---|---|---|
| `GET /incidents?include_dismissed=` | — | `[{incident_id, title, status, category, confidence, started_at, resolved_at, summary_status, visibility, needs_routing, ticket_count, customer_count, first_report, latest_report, departments}]` — managers see their department's clusters and unrouted ones, technicians only those assigned to them; open first, then resolved, then dismissed |
| `GET /incidents/{id}` | — | list fields + `summary` (narrative, why_related, common_symptoms, affected_services, observed_facts, hypotheses, possible_shared_cause, confidence_reasons, suggested_next_steps), `tickets` (each with `delivery: {status:"sent"\|"failed", error, attempted_at}` once a summary was sent), `affected_customers`, `reviews`, `all_departments`, `assignee`, `technicians`, `resolution` {resolution_note, root_cause, customer_summary, internal_summary, resolved_by}, `can` {review, departments, assign, resolve, visibility} |
| `POST /incidents/{id}/review` | `{action:"confirm"\|"investigate"\|"dismiss", note}` | M. detail. Never changes the linked tickets; not on a resolved incident. |
| `PUT /incidents/{id}/assignee` | `{technician_id \| null}` | M. The technician who may resolve it. |
| `POST /incidents/{id}/resolution/draft` | `{resolution_note, fixed_at, root_cause?}` | A manager or the assigned technician; **confirmed incidents only**. `fixed_at` is ISO; no offset = IST. Saves the inputs and asks the AI for a draft (customer + internal summary) from the incident facts and the note only. Returns detail + `ai_error` (null, or a message: inputs kept, write the summary by hand). |
| `PUT /incidents/{id}/resolution/summary` | `{customer_summary, internal_summary?, visibility?}` | Edit the draft before it's sent. |
| `POST /incidents/{id}/resolution/send` | `{customer_summary, mark_tickets_resolved=false}` | Approve & send. `customer_summary` must equal the saved text (409 otherwise). Posts it as a public reply from the Support Team on every linked ticket (email too if `EMAIL_ENABLED=true`), one delivery record per ticket; already-sent tickets are skipped, failures are recorded and don't stop the rest; calling again retries failures only. Incident → `resolved`, summary → `sent`. Optional: mark linked tickets Resolved. Never closes or merges tickets. |
| `PUT /incidents/{id}/visibility` | `{visibility:"affected_only"\|"public"}` | M. |
| `GET /public/incidents` | — | **No login.** Public incidents still in memory: `[{reference, status, category, started_at, resolved_at, summary}]` — nothing about customers or tickets. |
| `PUT /incidents/{id}/departments` | `{department_ids:[...]}` | M. detail. The manager must keep their own department on it. |
| `POST /incidents/run` | — | M. `{touched:[incident_id,...]}` — one correlation pass now |

## Try a ticket (technicians, managers)

| Method & path | Request | Response / notes |
|---|---|---|
| `POST /try-a-ticket` | `{ticket_text, channel?, customer_id?}` | pipeline trace without saving (category, confidence, route, route_reason, language_tags, ai_response); 503 if the AI is down. Customers and the admin get 403. The page is `/try-a-ticket`. |

## Admin

Every admin endpoint checks the admin role on the server: a customer,
technician or manager gets 403, no token 401.

Temporary passwords are generated by the server: a create or reset
response carries `temporary_password` once (`Cache-Control: no-store`).
Only a bcrypt hash is stored, it never appears in a list or the audit log,
and the account must change it at first sign-in.

| Method & path | Request | Notes |
|---|---|---|
| `GET /admin/users?role=&q=&status=active\|inactive` | — | customers, technicians and managers: `[{user_id, role, name, email, active, must_change_password, department_id, department_name, ticket_count}]`. Never the admin |
| `POST /admin/users` | `{name, email, role:"customer"\|"technician"\|"manager", department_id?}` | the user plus `temporary_password` (once); `role:"admin"` → 400 |
| `PATCH /admin/users/{id}` | any of `{name, email, role, department_id}` | staff role technician↔manager only; a customer can't become staff (400); the admin account → 403 |
| `POST /admin/users/{id}/deactivate` · `/activate` | — | deactivated: can't sign in, live sessions stop; tickets kept |
| `POST /admin/users/{id}/reset-password` | — | `temporary_password` (once); the old password stops working |
| `GET /admin/dashboard?since=&until=&priority=` | — | tickets created in the range (dates or ISO, UTC, default last 30 days, max 366): `{total, by_status, by_priority, over_time:[{date, created, resolved}], open_by_priority, open_by_technician:[{technician_id, name, count}], response_times:{<priority>:{tickets, avg_first_response_minutes, first_responses, avg_resolution_minutes, resolved}}}` |
| `GET /admin/sla?since=&until=&priority=` | — | `{enabled, at_risk_percent, calendar, tickets_with_sla, by_priority:{<p>:{tickets, first_response:{met, breached, percent}, resolution:{…}, breached, at_risk, within_sla}}, totals, watchlist:[{ticket_id, subject, priority, status, technician_name, next_due_at, sla}]}` |
| `POST /admin/tickets/{id}/priority` | `{priority}` | SLA due times recalculated from creation; audited |
| `GET /admin/tickets` | — | every ticket, every field, plus `sla` |
| `GET /admin/technicians` | — | active technicians with active/resolved counts |
| `POST /admin/tickets/{id}/reassign` | `{technician_id\|null}` | null returns it to the unassigned queue |
| `GET /admin/staff` | — | `[{staff_id, name, email, role, active, must_change_password, department_id, department_name}]` |
| `POST /admin/staff` | `{name, email, role:"technician"\|"manager", department_id?}` | plus `temporary_password` (once); forced change at first sign-in |
| `PATCH /admin/staff/{id}` | any of `{name, email, role, department_id}` | 409 if a technician with open tickets would change role; a role change signs them out |
| `POST /admin/staff/{id}/deactivate` · `/activate` · `/reset-password` | — | reset returns `temporary_password` (once) |
| `GET /admin/customers?q=` | — | `[{customer_id, name, email, active, must_change_password, ticket_count, chat_count}]` |
| `POST /admin/customers` | `{name, email}` | plus `temporary_password` (once) |
| `PATCH /admin/customers/{id}` | any of `{name, email}` | |
| `POST /admin/customers/{id}/deactivate` · `/activate` · `/reset-password` | — | reset returns `temporary_password` (once) |
| `GET /admin/departments` | — | `{departments:[{department_id, name, categories, manager_ids}], categories}` |
| `POST /admin/departments` · `PUT /admin/departments/{id}` | `{name, categories}` | a category may belong to one department only |
| `DELETE /admin/departments/{id}` | — | its managers are left without a department |
| `GET /admin/settings` | — | `{sections:{routing, incidents, statuses, sla}, defaults, categories, language_tags}` |
| `PUT /admin/settings/{routing\|incidents\|statuses\|sla}` | the whole section as JSON | validated; 422 with reasons |
| `DELETE /admin/settings/{section}` | — | back to env/default values |
| `GET /admin/audit?entity=&entity_id=&action=&actor_id=&since=&until=&limit=&offset=` | — | `{total, events:[{id, actor_id, actor_name, action, entity, entity_id, detail, at}]}`; `action` is a prefix match |
| `GET /admin/chats/{session_id}` | — | Chat state (status, categories, route, clarification count, escalated ticket), `message_counts` by role, and `ai_failures:[{at, kind, code, attempts, latency_ms, operation, outcome}]`. No message text. 404 unknown chat |

Settings sections:

- `routing`: `{confidence_threshold, high_risk_confidence_threshold, high_risk_categories:[...], escalation_language_tags:[...], max_clarification_attempts}`
- `incidents`: `{window_hours, min_tickets, similarity, routing_confidence}`
- `statuses`: `{open, in_progress, pending_customer, resolved, closed}` (labels)
- `sla`: `{enabled (default false), calendar:"24x7", at_risk_percent (75), urgent|high|medium|low:{first_response_minutes, resolution_minutes}}`; resolution can't be shorter than first response. Applies to tickets created while enabled.

Ticket `sla` (admin ticket list, staff list rows and workspace; never customer endpoints), null without an SLA:
`{state, within_sla, first_response:{target_minutes, due_at, done_at, used_ratio, state}, resolution:{…, paused}}`, states `on_track | at_risk | breached | met | paused`. The resolution clock pauses while Pending Customer and while Resolved/Closed (a reopen resumes it).
