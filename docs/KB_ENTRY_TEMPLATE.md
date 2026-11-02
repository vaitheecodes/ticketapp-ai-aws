# Knowledge-base entry template

New support answers go in `data/kb/new_entries.json`. They're written by a
person, never generated. The AI only rephrases what an entry says.

`data/kb/kb_entries.json` holds the 386 entries exported from the data
pipeline. Don't edit it by hand: `scripts/export_kb.py` regenerates it.

## Workflow

1. Add an entry to `data/kb/new_entries.json`. Copy the example entry, then
   set `"example"` to `false` or remove that field.
2. Run the validator and fix every `ERROR`:

   ```bash
   .venv/bin/python scripts/validate_kb.py
   ```

3. Restart the backend. The KB is loaded once per process.

The app skips any entry with an error, so a broken entry never reaches a
customer. It logs one warning that points you to the validator.

## Format

```json
{
  "entries": [
    {
      "kb_entry_id": "KB-N0002",
      "category": "AGENT",
      "intent": "agent_not_reporting",
      "questions": [
        "first way a customer asks it",
        "second way",
        "third way"
      ],
      "answer": "the support answer",
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
| `answer` | yes | At least 40 characters, written in plain text. It must contain no `EXAMPLE` text and no `[placeholders]`. |
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
