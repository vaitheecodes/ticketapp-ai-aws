# Knowledge-base entry template

New support answers go in `data/kb/new_entries.json`. They're written by a
person, never generated. The AI only rephrases what an entry says.

`data/kb/kb_entries.json` holds the 386 entries exported from the data
pipeline. Don't edit it by hand: `scripts/export_kb.py` regenerates it.

## Workflow

Every entry has a `status`. A `draft` is never used by the app. Only an
`approved` entry is used, and only once it passes the validator. The drafts
listed in `docs/KB_COVERAGE.md` were prepared from Site24x7's official
documentation. Each one waits for a person to review it, write or correct the
answer, and approve it.

1. Review the drafts with the interactive reviewer:

   ```bash
   .venv/bin/python scripts/kb_review.py
   ```

   It shows each draft's area, category, intent, questions, answer,
   `source_url` and `collect` list. You can then:

   - **approve:** pick the category, say what the entry is specific to, and
     type `yes` to confirm. "Specific to" is recorded per intent: marking
     `agent_installation` as specific to `Linux` and `Windows` makes the
     chat ask "Linux or Windows?" when a how-to names neither (change it
     later with `--retag ID`).
   - **edit:** change the answer text, and optionally the `source_url`.
   - **reject:** move the entry to `data/kb/rejected_entries.json`.
   - **skip:** leave it as a draft.

   Each decision is validated and saved atomically straight away, so you can
   stop at any point. Nothing is approved without the typed `yes`. To work
   through part of the list, use `--area "Databases"`, `--id KB-N0101` or
   `--list`.
2. You can also add a new entry by copying the example and removing
   `"example"`. Check the answer against the page in `source_url` before
   approving it.
3. Run the validator and fix every `ERROR`. It also lists the drafts by area:

   ```bash
   .venv/bin/python scripts/validate_kb.py
   ```

4. Restart the backend. The KB is loaded once per process.

The app skips any entry with an error, so a broken entry never reaches a
customer. It logs one warning that points you to the validator.

## Format

```json
{
  "entries": [
    {
      "kb_entry_id": "KB-N0002",
      "status": "approved",
      "area": "Network access and connectivity",
      "category": "AGENT",
      "intent": "agent_not_reporting",
      "monitor_type": "Server agent",
      "handling": "answer",
      "questions": [
        "first way a customer asks it",
        "second way",
        "third way"
      ],
      "collect": ["what to ask the customer for"],
      "answer": "the support answer",
      "source_url": "https://www.site24x7.com/help/...",
      "notes": "optional, internal"
    }
  ]
}
```

| Field | Required | Rules |
|---|---|---|
| `kb_entry_id` | yes | `KB-N` plus 4 digits, such as `KB-N0002`. Must be unique in this file and must not be used in `kb_entries.json`. |
| `category` | yes | One of the 17 categories: ACCOUNT, AGENT, ALERTS, APM, APPLICATION_MONITORING, CLOUD, CONTAINERS, DATA, INTEGRATION, LOGS, MONITORING, NETFLOW, REPORTING, SERVER_MONITORING, SNMP, SUPPORT, WEB_MONITORING. |
| `intent` | yes | `lower_snake_case`. Reuse an existing intent of that category where one fits. A new intent is allowed, and the validator warns so you can confirm it. |
| `questions` | yes | A list of non-empty phrasings of the same question. Use 3 or more, because retrieval matches on question wording: include a formal one, a casual one and a short one. Don't repeat a phrasing, and don't reuse a question from another entry. |
| `status` | yes | `draft` (never used) or `approved` (used once valid). The example entry doesn't need one. |
| `answer` | approved: yes; draft: optional | At least 40 characters, written in plain text. It must contain no `EXAMPLE` text and no `[placeholders]`. A draft may leave it empty, to be written. |
| `source_url` | on a draft with an answer: yes | The official page the answer paraphrases: an https link on `www.site24x7.com` or `support.site24x7.com`. An approved entry without one gets a warning. |
| `area` | draft: yes | The group the drafts report uses, such as "Databases". |
| `monitor_type` | no | The monitor or product the entry is about. |
| `handling` | no | `answer` (the answer resolves the question) or `escalate_with_info` (it needs someone with account access: collect the details, then hand over). |
| `kind` | no | `how-to` (doing a task: add, set up, configure, generate) or `troubleshooting` (fixing a fault). A how-to question is answered only from an entry about doing that task; `kind` says so directly instead of leaving it to the wording of the questions (a question with a fault word such as "missing" otherwise counts as troubleshooting). |
| `collect` | no | A list of what to ask the customer for. These are details the customer can give, never account identifiers. |
| `example` | no | `true` marks a sample entry. It's checked for shape only and never used. |
| `notes` | no | Internal notes, such as the source or reviewer. Customers and the AI never see them. |

Any other field causes a warning and is ignored.

## Writing the answer

- Write it as you would say it in a chat: no greeting, sign-off, signature
  or placeholders. Chat replies are sent as they are. Ticket replies get the
  greeting and signature added automatically.
- Give numbered steps only when the customer has to do something, one action
  per step.
- State only what is true for every customer who asks. The AI uses the
  answer as its only source and is told not to add facts.
- If something depends on the customer's setup, say what to check, not what
  the result will be.

## Categories and specificity (`data/kb/kb_registry.json`)

When you approve an entry, the reviewer records two things in
`data/kb/kb_registry.json`:

- **Category:** one of the classifier's 17 categories, or a new one you add.
  A new category only labels KB entries. The classifier never returns it,
  and a KB match never moves a ticket into it, until it is added to the code
  (`ai_service/schemas.py`, departments and the frontend).
- **Specific to:** which monitor type, integration or product the entry
  applies to, chosen from the known terms or added as a new term with a
  pattern. The AI only grounds a reply on the entry once the customer has
  mentioned one of these terms. Until then it asks whether the entry
  applies. `none` registers the entry as generic.

A new category or term must not repeat an existing name, in any case: pick
the existing one from the list instead (the reviewer says which number it
is). A new term's pattern must not be empty and must not match an empty
message, since such a pattern would count as "mentioned" in every message.
A registry term that repeats a built-in name is ignored, and the built-in
pattern is kept.

To change an approved entry's category or terms later, without approving
it again:

```bash
.venv/bin/python scripts/kb_review.py --retag KB-N0103
```

It offers the same choices and asks you to type `yes`. The entry stays
approved, keeps its `reviewed_at`, and gets a `retagged_at`. "Specific to"
is recorded per intent, so it applies to every entry with that intent (the
reviewer lists them).

To change an approved entry's answer (and optionally its `source_url`):

```bash
.venv/bin/python scripts/kb_review.py --edit KB-N0140
```

Type the new answer line by line and end with a line containing only `.`.
Line breaks are kept. The reviewer validates the entry, shows the result
and saves only after you type `yes`. The entry stays approved, keeps its
`reviewed_at`, and gets an `edited_at`.

`backend/topic_terms.py` merges the registry with its built-in terms. Restart
the backend after approving, retagging or editing.

## How retrieval uses entries

- Every question phrasing is indexed. A match returns the entry's answer as
  grounding for the AI.
- Entries in the same category and intent whose answers are at least 85%
  the same, word for word, count as one topic. Search results list each
  topic once. The validator warns when a new answer is that close to an
  existing one.
- A best match scoring below `KB_MIN_SCORE` (default 0.35) counts as no close
  match. The AI then asks one clarifying question or offers a ticket instead
  of answering from general knowledge.
