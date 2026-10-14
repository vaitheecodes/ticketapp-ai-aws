# ticketapp-ai

A small-scale clone of an enterprise AI customer-experience platform
(inspired by [Thunai.ai](https://thunai.ai)), demonstrating a full data
engineering + AI agent pipeline for support-ticket automation: raw data →
warehouse → a LangGraph agent that classifies, resolves, or escalates
tickets.

## Architecture

```
data/raw/*.csv
      |
      v
[bronze]  raw ingest + lineage metadata
      |
      v
[silver]  entity filling + synthetic ticket/resolution metadata
      |
      v
[gold]    DuckDB warehouse + CX aggregate views
      |
      v
LangGraph agent   ingest -> classify -> retrieve -> decide_route
      |                                                  |
      |                                    +-------------+-------------+
      |                                    |                           |
      |                             generate_response              escalate
      |                                    |                           |
      |                                    +-------------+-------------+
      |                                                  |
      +--------------------------------------------  log_outcome
                        |
                        v
                 FastAPI (planned)  ->  [Auto-resolve | Escalate]
```

## Data layer

**Source:** `data/raw/Site24x7_Customer_Support_Training_Dataset_27K.csv`
— 26,872 rows, 27 intents under 17 categories, all genuine Site24x7
monitoring-product support topics (monitor setup/troubleshooting,
alerting, integrations, agent installation, licensing, data retention).
It's an instruction-tuning dataset (`flags, instruction, category,
intent, response`), not ticket data, so the silver stage layers
realistic synthetic operational metadata on top of the genuine support
language. **The real knowledge-base size is 386 unique instructions** —
each is duplicated ~5x under different `flags` values (a
dataset-generation artifact, not independent tickets), so the 26,872 row
count overstates the raw content; the silver stage tags every ticket
with the canonical `kb_entry_id` (one of the 386) it was drawn from so
downstream consumers (the eval track's held-out split, repeat-contact
analysis) never mistake flag-variant duplication for real ticket volume
or repeat customer contact.

| Stage | Script | What it does |
|---|---|---|
| Bronze | `01_bronze_ingest.py` | Lands the raw CSV as-is (all-string, no coercion), adds lineage columns (`row_id`, `source_file`, `ingested_at`) and a `_manifest.json` (row/column counts, sha256 checksum). Partitioned by ingest date. |
| Silver | `02_silver_transform.py` | Decodes `flags` into readable `language_tags`; fills the dataset's one entity placeholder (`{{monitor name}}` / `{{Monitor Name}}`, case varies) with a realistic Site24x7-style monitor name (`placeholder_fill.py`, consistent per entity within a ticket); generates synthetic ticket metadata (timestamps, channel, customer_id, priority) and resolution metadata (resolved_by, status, handle time, CSAT); tags each ticket with its canonical `kb_entry_id`. Seeded (42) for reproducibility. |
| Gold | `03_gold_load.py` | Loads silver tables into a local DuckDB warehouse, builds 3 CX aggregate views, and re-emits both tables as Hive-partitioned Parquet (ready for an `aws s3 sync` + Athena external table, no reshaping needed). |

**`tickets`** — `ticket_id`, `created_at`, `channel` (email/chat/voice),
`customer_id`, `category`, `intent`, `instruction_text`,
`instruction_entities`, `priority` (low/medium/high/urgent),
`language_tags`, `kb_entry_id`.

**`resolutions`** (FK `ticket_id`) — `response_text`, `response_entities`,
`resolved_by` (ai/human), `resolution_status` (resolved/escalated/pending),
`handle_time_seconds`, `csat_score` (1–5, null while pending).

Full design rationale for the synthetic fields (why priority/resolved_by/
handle_time are shaped the way they are, the S3+Athena extension path)
is documented inline in `src/pipeline/`.

## Agent layer

A [LangGraph](https://github.com/langchain-ai/langgraph) state machine
(`src/agent/`) that takes an incoming ticket through classification,
retrieval, and routing:

| Node | Role |
|---|---|
| `ingest_ticket` | Normalizes the incoming ticket fields, starts the trace. |
| `classify_intent` | Assigns intent + category + confidence. |
| `retrieve_knowledge` | Looks up the canonical resolution + entity slots for the classified intent. |
| `decide_route` | Conditional edge: `auto_resolve` vs `escalate`. |
| `generate_response` | Auto-resolve path — drafts a customer-facing reply grounded in the retrieved KB entry. |
| `escalate` | Escalation path — produces a structured handoff summary (ticket text, intent, confidence, reason) for a human agent. |
| `log_outcome` | Terminal node; will persist the outcome to a `resolutions_ai` table. |

**Routing logic** (`decide_route`), checked in order:
1. **Category risk** — ACCOUNT/DATA require a higher confidence bar
   (≥0.90) than other categories before auto-resolving, since a wrong
   auto-response on licensing/billing changes or data retention/deletion
   is costlier.
2. **Language tags** — offensive language, coordinated/compound phrasing,
   or negation in the ticket forces escalation regardless of confidence.
3. **Confidence threshold** — anything below 0.75 escalates.

**Current status: stub nodes, no model calls yet.** `classify_intent` is
a keyword lookup, `retrieve_knowledge` returns a canned entry, and
`generate_response` formats a template string — this is deliberate,
checkpoint-driven scaffolding to validate the graph shape and routing
logic on dummy data (`src/agent/run_demo.py`) before wiring in real
Amazon Bedrock (Claude) calls for classification and generation.

## Tech stack

pandas, DuckDB, PyArrow (Parquet), Faker · LangGraph · Amazon Bedrock
(Claude, planned) · FastAPI (planned) · pytest · Docker (planned,
target: ECS Fargate) · optional PySpark/Databricks port for the silver
transform.

## How to run

```bash
pip install -r requirements.txt

# data pipeline
python src/pipeline/01_bronze_ingest.py
python src/pipeline/02_silver_transform.py
python src/pipeline/03_gold_load.py

# agent skeleton (stub nodes, dummy tickets)
python src/agent/run_demo.py

# tests
pytest tests/ -v
```

Query the warehouse directly:

```bash
python -c "import duckdb; duckdb.connect('data/gold/warehouse.duckdb').sql('SELECT * FROM v_deflection_rate_by_intent').show()"
```

FastAPI server and eval notebook: not built yet (see roadmap).

## Status / roadmap

- [x] Bronze → silver → gold data pipeline, DuckDB warehouse + 3 CX views
- [x] Data-quality test suite (33 checks), exploratory notebook
- [x] LangGraph skeleton with stub nodes, routing logic validated on dummy data
- [ ] KB / held-out test split — split the 386 unique `kb_entry_id`
      values 80/20 first, then map tickets back to an auditable `split`
      column (never split on raw rows, which would leak near-duplicate
      flag-variants of the same question across the split)
- [ ] Real Bedrock Claude calls for `classify_intent` and `generate_response`
- [ ] FastAPI layer (`POST /tickets`, `GET /metrics/deflection`, `GET /tickets/{id}`)
- [ ] `notebooks/eval.ipynb` — deflection rate, simulated handle time, LLM-judge quality score on the held-out set

## Why this mirrors an enterprise CX platform

This project demonstrates the two things AI CX platforms like Thunai.ai,
Zendesk AI, and Intercom Fin are actually sold on: **deflection** (the
`v_deflection_rate_by_intent` gold view, and eventually the agent's own
auto-resolve rate on held-out tickets) and **agent-assist** (routing
simple, low-risk contacts to instant AI resolution while sensitive or
low-confidence ones reach a human faster, with a full handoff summary
instead of a cold transfer). The `decide_route` policy — confidence
threshold, risk-weighted categories, language-tag overrides — is a
simplified version of the same triage logic these platforms position as
their core value proposition: automate the 70–80% of contacts that are
repetitive, and get the rest to a human with context attached.
