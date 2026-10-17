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
invalid input, `503` AI provider unavailable (simulator only).

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
| `GET /meta` | public | `{status_labels:{open, in_progress, pending_customer, resolved, closed}, categories:[...]}` |
| `GET /categories` | public | `["ACCOUNT", "AGENT", ...]` (the 17 KB categories) |
| `GET /debug/routing-policy` | public (dev aid) | live routing policy: thresholds, high-risk categories, escalation tags, max clarifying rounds, decision order |

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
| `POST /tickets/{id}/attachments` | C (own) | multipart: `message_id`, `files` (≤5 per message, ≤10 MB, png/jpg/pdf/txt/log, contents must match) | `[{attachment_id, filename, content_type, size_bytes}]` |
| `GET /tickets/{id}/attachments/{attachment_id}` | C (own, visible messages) / T / M / A | — | file download (`Content-Disposition: attachment`) |

## Technician workspace

| Method & path | Role | Request | Response / notes |
|---|---|---|---|
| `GET /staff/tickets?view=&q=&priority=&status=` | T | `view`: `unassigned` (oldest first) \| `mine` \| `recent` \| `open_pending` | `[{ticket_id, subject, status, priority, ticket_type, category, created_at, last_activity, customer_name, customer_email, assigned_technician_id, assignee_name, preview}]` (tickets with the support team only) |
| `GET /staff/tickets/{id}` | T, M (own department or linked incident), A | — | `{ticket:{..., description, customer, assignee}, messages:[... visibility, customer_visible, author ...], ai:{summary, suggestions, case_summary}, chat, incidents, status_history, assignment_history, can:{claim, reply, change_status, note}}` |
| `POST /staff/tickets/{id}/claim` | T | — | workspace. 409 if someone else claimed it first (atomic). |
| `POST /staff/tickets/{id}/status` | T (owner) | `{status}` | workspace; adds a customer-visible status event |
| `POST /staff/tickets/{id}/messages` | T | `{body, visibility:"public_reply"\|"internal_note", subject?, set_status?}` | workspace. Public replies: owner only. Notes: any technician; never shown to customers. |
| `GET /chat-reviews?status=` | T, A | — | AI-resolved chats with summary + transcript |
| `POST /chat-reviews/{id}` | T, A | `{status:"verified"\|"closed"}` | `pending_review → verified → closed` |

## Manager

| Method & path | Role | Response / notes |
|---|---|---|
| `GET /manager/departments` | M | `[{department_id, name, categories, manager_ids}]` (zero or one) |
| `GET /manager/overview` | M | `{categories, tickets:[...], workload:[{staff_id, name, open, in_progress, pending_customer, resolved}], unassigned}` for the manager's department. Read-only. |

## Incidents (manager, admin)

| Method & path | Request | Response / notes |
|---|---|---|
| `GET /incidents?include_dismissed=` | — | `[{incident_id, title, status, category, confidence, needs_routing, ticket_count, customer_count, first_report, latest_report, departments}]` — managers see their department's clusters and unrouted ones; open first, then confidence, then recency |
| `GET /incidents/{id}` | — | list fields + `summary` (narrative, why_related, common_symptoms, affected_services, observed_facts, hypotheses, possible_shared_cause, confidence_reasons, suggested_next_steps), `tickets`, `affected_customers`, `reviews`, `all_departments` |
| `POST /incidents/{id}/review` | `{action:"confirm"\|"investigate"\|"dismiss", note}` | detail. Never changes the linked tickets. |
| `PUT /incidents/{id}/departments` | `{department_ids:[...]}` | detail. Managers must keep their own department; empty = Needs routing (admin). |
| `POST /incidents/run` | — | `{touched:[incident_id,...]}` — one correlation pass now |

## Admin

| Method & path | Request | Notes |
|---|---|---|
| `GET /admin/tickets` | — | every ticket, every field |
| `GET /admin/technicians` | — | active technicians with active/resolved counts |
| `POST /admin/tickets/{id}/reassign` | `{technician_id\|null}` | null returns it to the unassigned queue |
| `POST /admin/simulate-ticket` | `{ticket_text, channel?, customer_id?}` | pipeline trace without saving; 503 if the AI is down |
| `GET /admin/staff` | — | `[{staff_id, name, email, role, active, must_change_password, department_id, department_name}]` |
| `POST /admin/staff` | `{name, email, role:"technician"\|"manager", temporary_password, department_id?}` | forced password change at first sign-in |
| `PATCH /admin/staff/{id}` | any of `{name, email, role, department_id}` | 409 if a technician with open tickets would change role; a role change signs them out |
| `POST /admin/staff/{id}/deactivate` · `/activate` · `/reset-password` | reset: `{temporary_password}` | |
| `GET /admin/customers?q=` | — | `[{customer_id, name, email, active, must_change_password, ticket_count, chat_count}]` |
| `POST /admin/customers` | `{name, email, temporary_password}` | |
| `PATCH /admin/customers/{id}` | any of `{name, email}` | |
| `POST /admin/customers/{id}/deactivate` · `/activate` · `/reset-password` | reset: `{temporary_password}` | |
| `GET /admin/departments` | — | `{departments:[{department_id, name, categories, manager_ids}], categories}` |
| `POST /admin/departments` · `PUT /admin/departments/{id}` | `{name, categories}` | a category may belong to one department only |
| `DELETE /admin/departments/{id}` | — | its managers are left without a department |
| `GET /admin/settings` | — | `{sections:{routing, incidents, statuses}, defaults, categories, language_tags}` |
| `PUT /admin/settings/{routing\|incidents\|statuses}` | the whole section as JSON | validated; 422 with reasons |
| `DELETE /admin/settings/{section}` | — | back to env/default values |
| `GET /admin/audit?entity=&entity_id=&action=&actor_id=&since=&until=&limit=&offset=` | — | `{total, events:[{id, actor_id, actor_name, action, entity, entity_id, detail, at}]}`; `action` is a prefix match |

Settings sections:

- `routing`: `{confidence_threshold, high_risk_confidence_threshold, high_risk_categories:[...], escalation_language_tags:[...], max_clarification_attempts}`
- `incidents`: `{window_hours, min_tickets, similarity, routing_confidence}`
- `statuses`: `{open, in_progress, pending_customer, resolved, closed}` (labels)
