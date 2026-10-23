"""Export the 386 canonical KB entries from the silver Parquet tables to
data/kb/kb_entries.json, the runtime format backend/kb_retrieval.py loads
with the standard library (so the deployed backend doesn't need pandas or
pyarrow). Same selection and order as before: tickets joined to their
resolution, first row per kb_entry_id in Parquet order.

    .ven/bin/python scripts/export_kb.py

Re-run after the pipeline regenerates data/silver/. tests/test_kb_export.py
checks the file is current and that retrieval results are identical.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TICKETS = ROOT / "data" / "silver" / "tickets.parquet"
RESOLUTIONS = ROOT / "data" / "silver" / "resolutions.parquet"
OUTPUT = ROOT / "data" / "kb" / "kb_entries.json"


def entries_from_parquet() -> list[dict]:
    import pandas as pd

    tickets = pd.read_parquet(TICKETS, columns=["ticket_id", "kb_entry_id", "category", "intent",
                                                "instruction_text"])
    resolutions = pd.read_parquet(RESOLUTIONS, columns=["ticket_id", "response_text"])
    merged = tickets.merge(resolutions, on="ticket_id", how="left")
    canonical = merged.drop_duplicates(subset="kb_entry_id", keep="first")
    return [
        {"kb_entry_id": row.kb_entry_id, "category": row.category, "intent": row.intent,
         "instruction": row.instruction_text, "response": row.response_text or ""}
        for row in canonical.itertuples(index=False)
    ]


def render(entries: list[dict]) -> str:
    return json.dumps({"source": "data/silver (tickets + resolutions), first row per kb_entry_id",
                       "count": len(entries), "entries": entries}, ensure_ascii=False, indent=1) + "\n"


if __name__ == "__main__":
    entries = entries_from_parquet()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(entries), encoding="utf-8")
    print(f"wrote {len(entries)} entries, {OUTPUT.stat().st_size / 1024:.0f} KB -> {OUTPUT.relative_to(ROOT)}")
