"""Check data/kb/new_entries.json before the app uses it.

    .venv/bin/python scripts/validate_kb.py [path]

Checks every entry against docs/KB_ENTRY_TEMPLATE.md: required fields,
status (draft / approved), id format and uniqueness, category, intent,
question variants, answer length, leftover EXAMPLE/[placeholder] text,
source_url (required, and an official Site24x7 page, on any draft that
has an answer), and duplicates (ids, questions, near-identical answers)
within the file and against data/kb/kb_entries.json. Reports drafts by
area: answered (with a source) versus empty. Only "approved" entries are
used by the app; drafts and entries marked "example": true never are.

Exit code 0 when there are no errors (warnings are fine), 1 otherwise.
The app skips entries with errors, so a broken entry never reaches a
customer; restart the backend to load new entries.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import kb_new_entries, kb_retrieval  # noqa: E402


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else kb_new_entries.NEW_ENTRIES_PATH
    if not path.exists():
        print(f"{path}: not found")
        return 1
    existing = json.loads(kb_retrieval.KB_PATH.read_text(encoding="utf-8"))["entries"]
    report = kb_new_entries.load(existing, path)

    usable_ids = sorted({e["kb_entry_id"] for e in report.entries})
    print(f"{path.name}: {report.checked} entr{'y' if report.checked == 1 else 'ies'} checked, "
          f"{report.examples} example(s) skipped, {len(report.drafts)} draft(s) (not used), "
          f"{len(usable_ids)} approved and usable ({len(report.entries)} question variants)")
    if report.drafts:
        print("  drafts by area (answered = has an answer with a source_url; empty = to be written):")
        areas: dict[str, list[dict]] = {}
        for d in report.drafts:
            areas.setdefault(d["area"], []).append(d)
        for area in sorted(areas):
            items = areas[area]
            answered = [d["kb_entry_id"] for d in items if d["has_answer"]]
            empty = [d["kb_entry_id"] for d in items if not d["has_answer"]]
            escalate = sum(d["handling"] == "escalate_with_info" for d in items)
            print(f"    {area}: {len(items)} draft(s), {len(answered)} answered, {len(empty)} empty"
                  + (f", {escalate} escalate_with_info" if escalate else ""))
            if empty:
                print(f"      empty: {', '.join(empty)}")
    for line in report.errors:
        print(f"  ERROR    {line}")
    for line in report.warnings:
        print(f"  warning  {line}")
    if not report.errors and not report.warnings:
        print("  no problems found")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
