"""Review the draft KB entries in data/kb/new_entries.json, one at a time.

    .venv/bin/python scripts/kb_review.py [--area AREA] [--id KB-N0101] [--list]
    .venv/bin/python scripts/kb_review.py --retag KB-N0103
    .venv/bin/python scripts/kb_review.py --edit KB-N0140

For each draft it shows the area, category, intent, question wordings,
answer, source_url and "collect" list, then asks what to do:

  a  approve  pick the category (the classifier's 17, or a new KB-only
              one), say which monitor type / integration / product the
              entry is specific to (registered in data/kb/kb_registry.json
              for backend/topic_terms.py), then confirm by typing "yes".
              The entry is validated first; status becomes "approved" and
              reviewed_at is recorded.
  e  edit     replace the answer text (and optionally the source_url).
  r  reject   move the entry to data/kb/rejected_entries.json.
  s  skip     leave it as a draft.
  q  quit

--retag changes an approved entry's category and "specific to" terms
(same choices as approve, confirmed by typing "yes"); it stays approved,
keeps its reviewed_at and gets retagged_at. "Specific to" is recorded per
intent, so it applies to every entry with that intent.

--edit replaces an approved entry's answer (and optionally its
source_url) after a typed "yes"; it stays approved, keeps reviewed_at and
gets edited_at. Line breaks in an answer are kept.

A new category or term must not repeat an existing name (any case), and
a new term's regex must not be empty or match an empty message.

Nothing is ever approved without that typed confirmation. Every decision
is saved atomically straight away, so you can stop at any point. Restart
the backend afterwards to load approved entries.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import kb_new_entries, kb_registry, kb_retrieval, topic_terms  # noqa: E402
from backend.ai_service.schemas import CATEGORIES  # noqa: E402

REJECTED_PATH = ROOT / "data" / "kb" / "rejected_entries.json"


class ReviewError(Exception):
    """The decision can't be saved (the message says why)."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default):
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def existing_kb() -> list[dict]:
    return json.loads(kb_retrieval.KB_PATH.read_text(encoding="utf-8"))["entries"]


def find(data: dict, kb_id: str) -> tuple[int, dict]:
    for i, entry in enumerate(data["entries"]):
        if entry.get("kb_entry_id") == kb_id:
            return i, entry
    raise ReviewError(f"{kb_id} is not in the file")


def drafts(data: dict, area: str | None = None, kb_id: str | None = None) -> list[dict]:
    return [e for e in data["entries"]
            if e.get("status") == "draft" and not e.get("example")
            and (area is None or e.get("area", "").lower() == area.lower())
            and (kb_id is None or e.get("kb_entry_id") == kb_id)]


def entry_errors(data: dict, kb_id: str, registry: dict) -> list[str]:
    report = kb_new_entries.validate(data, existing_kb(), registry)
    return [e for e in report.errors if f"({kb_id})" in e or e.startswith("file:")]


def card(entry: dict, registry: dict) -> str:
    intent = entry.get("intent", "")
    registered = registry["intent_terms"].get(intent)
    built_in = topic_terms.INTENT_TERMS.get(intent)
    specific = registered if registered is not None else (sorted(built_in) if built_in is not None else None)
    lines = [
        "=" * 72,
        f"{entry.get('kb_entry_id')}  [{entry.get('status')}]  area: {entry.get('area')}",
        f"category: {entry.get('category')}   intent: {intent}   monitor type: {entry.get('monitor_type', '-')}",
        f"handling: {entry.get('handling', '-')}   kind: {entry.get('kind', '-')}   specific to: "
        + ("not registered yet" if specific is None else (", ".join(specific) or "nothing (generic)")),
        "questions:",
        *[f"  - {q}" for q in entry.get("questions", [])],
        "answer:",
        f"  {entry.get('answer') or '(empty: edit it before approving)'}",
        f"source_url: {entry.get('source_url') or '-'}",
        "collect:",
        *[f"  - {c}" for c in entry.get("collect", [])],
    ]
    return "\n".join(lines)


def known_labels(registry: dict) -> list[str]:
    return sorted(set(topic_terms.TERMS) | set(registry["terms"]))


def new_category_problem(name: str, registry: dict) -> str | None:
    """Why `name` can't be added as a new category (None: it can)."""
    if not kb_registry.CATEGORY_PATTERN.match(name):
        return "a new category must be UPPER_SNAKE_CASE, e.g. DATABASES"
    existing = kb_new_entries.allowed_categories(registry)
    for i, other in enumerate(existing, start=1):
        if other.upper() == name.upper():
            return f"category {other} already exists (number {i}): pick it instead of adding it"
    return None


def new_term_problem(label: str, pattern: str, known: list[str]) -> str | None:
    """Why a new term can't be added (None: it can)."""
    if not kb_registry.LABEL_PATTERN.match(label):
        return f"{label!r} is not a short plain label"
    for i, other in enumerate(known, start=1):
        if other.lower() == label.lower():
            return f"term {other!r} already exists (number {i}): pick it instead of adding it"
    problem = kb_registry.regex_problem(pattern)
    return f"the regex {problem}" if problem else None


def _tag(entry: dict, registry: dict, *, category: str, specific_to: list[str], new_category: bool,
         new_terms: dict[str, str] | None) -> None:
    """Record the category / terms in `registry` (in place); raises
    ReviewError for a duplicate name, an empty regex or an unknown term."""
    if new_category:
        problem = new_category_problem(category, registry)
        if problem:
            raise ReviewError(problem)
        registry["categories"].append(category)
    elif category not in kb_new_entries.allowed_categories(registry):
        raise ReviewError(f"unknown category {category!r}")
    for label, pattern in (new_terms or {}).items():
        problem = new_term_problem(label, pattern, known_labels(registry))
        if problem:
            raise ReviewError(problem)
        registry["terms"][label] = pattern
    known = set(known_labels(registry))
    unknown = [label for label in specific_to if label not in known]
    if unknown:
        raise ReviewError(f"unknown term(s): {', '.join(unknown)}")
    registry["intent_terms"][entry["intent"]] = sorted(set(specific_to))
    found = kb_registry.problems(registry)
    if found:
        raise ReviewError("; ".join(found))


# ---------------------------------------------------------------------------
# Decisions (pure: return the new file contents; the caller saves them)
# ---------------------------------------------------------------------------
def approve(data: dict, registry: dict, kb_id: str, *, category: str, specific_to: list[str],
            new_category: bool = False, new_terms: dict[str, str] | None = None,
            reviewed_at: str | None = None) -> tuple[dict, dict]:
    """The approved entry (status, category, reviewed_at) and the registry
    with its category / terms / intent mapping. Raises ReviewError with
    the validator's messages if the entry isn't valid."""
    data, registry = copy.deepcopy(data), copy.deepcopy(registry)
    index, entry = find(data, kb_id)
    if entry.get("status") != "draft":
        raise ReviewError(f"{kb_id} is not a draft")
    if not (entry.get("answer") or "").strip():
        raise ReviewError(f"{kb_id} has no answer: edit it before approving")
    _tag(entry, registry, category=category, specific_to=specific_to, new_category=new_category,
         new_terms=new_terms)
    entry = {**entry, "status": "approved", "category": category, "reviewed_at": reviewed_at or _now()}
    data["entries"][index] = entry
    errors = entry_errors(data, kb_id, registry)
    if errors:
        raise ReviewError("\n".join(errors))
    return data, registry


def retag(data: dict, registry: dict, kb_id: str, *, category: str, specific_to: list[str],
          new_category: bool = False, new_terms: dict[str, str] | None = None,
          retagged_at: str | None = None) -> tuple[dict, dict]:
    """An approved entry with a new category and "specific to" terms. It
    stays approved and keeps reviewed_at; retagged_at is recorded."""
    data, registry = copy.deepcopy(data), copy.deepcopy(registry)
    index, entry = find(data, kb_id)
    if entry.get("status") != "approved":
        raise ReviewError(f"{kb_id} is not approved: review it with the normal flow instead")
    _tag(entry, registry, category=category, specific_to=specific_to, new_category=new_category,
         new_terms=new_terms)
    entry = {**entry, "category": category, "retagged_at": retagged_at or _now()}
    data["entries"][index] = entry
    errors = entry_errors(data, kb_id, registry)
    if errors:
        raise ReviewError("\n".join(errors))
    return data, registry


def edit_approved(data: dict, registry: dict, kb_id: str, answer: str, source_url: str | None = None,
                  edited_at: str | None = None) -> dict:
    """An approved entry with a new answer (and source_url). It stays
    approved and keeps reviewed_at; edited_at is recorded."""
    _, entry = find(data, kb_id)
    if entry.get("status") != "approved":
        raise ReviewError(f"{kb_id} is not approved: edit it in the normal review flow instead")
    data = edit(data, registry, kb_id, answer, source_url)
    index, entry = find(data, kb_id)
    data["entries"][index] = {**entry, "edited_at": edited_at or _now()}
    return data


def sharing_intent(data: dict, entry: dict) -> list[str]:
    """Other new entries with the same intent (they share its terms)."""
    return [e["kb_entry_id"] for e in data["entries"]
            if e.get("intent") == entry.get("intent") and e.get("kb_entry_id") != entry.get("kb_entry_id")
            and not e.get("example")]


def edit(data: dict, registry: dict, kb_id: str, answer: str, source_url: str | None = None) -> dict:
    data = copy.deepcopy(data)
    index, entry = find(data, kb_id)
    entry = {**entry, "answer": answer.strip()}
    if source_url is not None:
        entry["source_url"] = source_url.strip()
    if entry.get("status") == "approved" and not entry["answer"]:
        raise ReviewError(f"{kb_id} is approved: its answer can't be empty")
    data["entries"][index] = entry
    errors = entry_errors(data, kb_id, registry)
    if errors:
        raise ReviewError("\n".join(errors))
    return data


def reject(data: dict, rejected: dict, kb_id: str, reason: str = "",
           rejected_at: str | None = None) -> tuple[dict, dict]:
    data, rejected = copy.deepcopy(data), copy.deepcopy(rejected)
    index, entry = find(data, kb_id)
    del data["entries"][index]
    rejected.setdefault("entries", []).append(
        {**entry, "status": "rejected", "rejected_at": rejected_at or _now(), "reject_reason": reason.strip()})
    return data, rejected


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------
class Reviewer:
    def __init__(self, entries_path: Path, registry_path: Path, rejected_path: Path,
                 ask=input, say=print):
        self.entries_path, self.registry_path, self.rejected_path = entries_path, registry_path, rejected_path
        self.ask, self.say = ask, say

    def _load(self):
        return load_json(self.entries_path, {"entries": []}), kb_registry.load(self.registry_path)

    def _choose_category(self, entry: dict, registry: dict) -> tuple[str, bool] | None:
        options = kb_new_entries.allowed_categories(registry)
        self.say("Category:")
        for i, name in enumerate(options, start=1):
            extra = "  (KB-only)" if name not in CATEGORIES else ""
            self.say(f"  {i:2}. {name}{extra}{'  <- current' if name == entry.get('category') else ''}")
        while True:
            answer = self.ask("Category number or name; Enter keeps the current one; 'new' adds one; "
                              "'c' cancels: ").strip()
            if answer == "":
                return entry["category"], False
            if answer.lower() == "c":
                return None
            if answer.lower() == "new":
                name = self.ask("New category name (UPPER_SNAKE_CASE): ").strip().upper()
                problem = new_category_problem(name, registry)
                if problem:
                    self.say(f"  Not added: {problem}.")
                    continue
                self.say("  Note: a new category labels KB entries only. The classifier keeps its 17 "
                         "categories until this one is added to the code.")
                if self.ask(f"  Add category {name}? [y/N]: ").strip().lower() == "y":
                    return name, True
                continue
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1], False
            if answer.upper() in options:
                return answer.upper(), False
            self.say("  Not one of the listed categories.")

    def _choose_terms(self, entry: dict, registry: dict) -> tuple[list[str], dict[str, str]] | None:
        intent = entry["intent"]
        current = registry["intent_terms"].get(intent)
        if current is None and intent in topic_terms.INTENT_TERMS:
            current = sorted(topic_terms.INTENT_TERMS[intent])
        known = known_labels(registry)
        self.say(f"Which monitor type / integration / product is {intent} specific to?")
        self.say("  The AI only grounds a reply on this entry once the customer has mentioned one of them.")
        for i, label in enumerate(known, start=1):
            self.say(f"  {i:2}. {label}")
        if current is not None:
            self.say(f"  current: {', '.join(current) or 'nothing (generic)'}")
        new_terms: dict[str, str] = {}
        while True:
            answer = self.ask("Numbers or labels, comma-separated; 'none' = generic; 'new' adds a term"
                              + ("; Enter keeps the current one" if current is not None else "")
                              + "; 'c' cancels: ").strip()
            if answer.lower() == "c":
                return None
            if answer == "" and current is not None:
                return list(current), new_terms
            if answer.lower() == "none":
                return [], new_terms
            if answer.lower() == "new":
                label = self.ask("  New term label (e.g. 'Nagios'): ").strip()
                pattern = self.ask("  Regex matched against the customer's words (e.g. \\bnagios\\b): ").strip()
                problem = new_term_problem(label, pattern, known)
                if problem:
                    self.say(f"  Not added: {problem}.")
                    continue
                new_terms[label] = pattern
                known = sorted(set(known) | {label})
                self.say(f"  Added {label}; now pick the terms (it is number {known.index(label) + 1}).")
                continue
            picked, bad = [], []
            for part in (p.strip() for p in answer.split(",") if p.strip()):
                if part.isdigit() and 1 <= int(part) <= len(known):
                    picked.append(known[int(part) - 1])
                elif part in known:
                    picked.append(part)
                else:
                    bad.append(part)
            if bad or not picked:
                self.say(f"  Not recognised: {', '.join(bad) or '(nothing picked)'}")
                continue
            return picked, new_terms

    def _approve(self, entry: dict) -> None:
        data, registry = self._load()
        if not (entry.get("answer") or "").strip():
            self.say("This draft has no answer: edit it before approving.")
            return
        category = self._choose_category(entry, registry)
        if category is None:
            return
        terms = self._choose_terms(entry, registry)
        if terms is None:
            return
        (name, is_new), (specific, new_terms) = category, terms
        self.say(f"About to approve {entry['kb_entry_id']}: category {name}"
                 f"{' (new, KB-only)' if is_new else ''}, specific to "
                 f"{', '.join(specific) or 'nothing (generic)'}.")
        if self.ask("Type 'yes' to approve: ").strip().lower() != "yes":
            self.say("Not approved.")
            return
        try:
            data, registry = approve(data, registry, entry["kb_entry_id"], category=name, specific_to=specific,
                                     new_category=is_new, new_terms=new_terms)
        except ReviewError as exc:
            self.say(f"Not approved, the entry is not valid:\n{exc}")
            return
        kb_registry.save(registry, self.registry_path)
        kb_registry.write_json_atomic(self.entries_path, data)
        self.say(f"Approved {entry['kb_entry_id']} and saved.")

    def retag(self, kb_id: str) -> int:
        try:
            return self._retag(kb_id)
        except EOFError:
            self.say("\nStopped. Nothing was changed.")
            return 0

    def _retag(self, kb_id: str) -> int:
        data, registry = self._load()
        try:
            _, entry = find(data, kb_id)
        except ReviewError as exc:
            self.say(str(exc))
            return 1
        if entry.get("status") != "approved":
            self.say(f"{kb_id} is not approved: review it without --retag.")
            return 1
        self.say(card(entry, registry))
        others = sharing_intent(data, entry)
        if others:
            self.say(f"Note: \"specific to\" is recorded per intent; {', '.join(others)} "
                     f"share {entry['intent']} and get the same terms.")
        category = self._choose_category(entry, registry)
        if category is None:
            self.say("Nothing was changed.")
            return 0
        terms = self._choose_terms(entry, registry)
        if terms is None:
            self.say("Nothing was changed.")
            return 0
        (name, is_new), (specific, new_terms) = category, terms
        self.say(f"About to retag {kb_id}: category {name}{' (new, KB-only)' if is_new else ''}, "
                 f"specific to {', '.join(specific) or 'nothing (generic)'}. It stays approved.")
        if self.ask("Type 'yes' to save: ").strip().lower() != "yes":
            self.say("Nothing was changed.")
            return 0
        try:
            data, registry = retag(data, registry, kb_id, category=name, specific_to=specific,
                                   new_category=is_new, new_terms=new_terms)
        except ReviewError as exc:
            self.say(f"Not saved:\n{exc}")
            return 1
        kb_registry.save(registry, self.registry_path)
        kb_registry.write_json_atomic(self.entries_path, data)
        self.say(f"Retagged {kb_id} and saved. Restart the backend to use it.")
        return 0

    def _read_answer(self) -> str | None:
        """Answer text typed line by line, ending with a '.' line (None: cancelled)."""
        self.say("New answer text; finish with a line containing only '.' (an empty first line cancels):")
        lines = []
        while True:
            line = self.ask("")
            if line.strip() == "." or (not lines and line.strip() == ""):
                break
            lines.append(line.rstrip())
        return "\n".join(lines) if lines else None

    def edit_approved(self, kb_id: str) -> int:
        try:
            return self._edit_approved(kb_id)
        except EOFError:
            self.say("\nStopped. Nothing was changed.")
            return 0

    def _edit_approved(self, kb_id: str) -> int:
        data, registry = self._load()
        try:
            _, entry = find(data, kb_id)
        except ReviewError as exc:
            self.say(str(exc))
            return 1
        if entry.get("status") != "approved":
            self.say(f"{kb_id} is not approved: edit it without --edit.")
            return 1
        self.say(card(entry, registry))
        answer = self._read_answer()
        if answer is None:
            self.say("Nothing was changed.")
            return 0
        source = self.ask(f"source_url (Enter keeps {entry.get('source_url') or 'none'}): ").strip()
        try:
            new_data = edit_approved(data, registry, kb_id, answer, source or None)
        except ReviewError as exc:
            self.say(f"Not saved, the entry is not valid:\n{exc}")
            return 1
        _, new_entry = find(new_data, kb_id)
        self.say("New answer:")
        self.say(new_entry["answer"])
        self.say(f"source_url: {new_entry.get('source_url') or '-'}")
        if self.ask(f"Type 'yes' to save {kb_id} (it stays approved): ").strip().lower() != "yes":
            self.say("Nothing was changed.")
            return 0
        kb_registry.write_json_atomic(self.entries_path, new_data)
        self.say(f"Saved {kb_id}. Restart the backend to use it.")
        return 0

    def _edit(self, entry: dict) -> None:
        data, registry = self._load()
        answer = self._read_answer()
        if answer is None:
            self.say("Edit cancelled.")
            return
        source = self.ask(f"source_url (Enter keeps {entry.get('source_url') or 'none'}): ").strip()
        try:
            data = edit(data, registry, entry["kb_entry_id"], answer, source or None)
        except ReviewError as exc:
            self.say(f"Not saved, the entry is not valid:\n{exc}")
            return
        kb_registry.write_json_atomic(self.entries_path, data)
        self.say("Answer saved (still a draft).")

    def _reject(self, entry: dict) -> None:
        data, _ = self._load()
        reason = self.ask("Reason (optional): ")
        if self.ask(f"Move {entry['kb_entry_id']} to rejected_entries.json? [y/N]: ").strip().lower() != "y":
            self.say("Not rejected.")
            return
        rejected = load_json(self.rejected_path, {"entries": []})
        data, rejected = reject(data, rejected, entry["kb_entry_id"], reason)
        kb_registry.write_json_atomic(self.rejected_path, rejected)  # first: never lose the entry
        kb_registry.write_json_atomic(self.entries_path, data)
        self.say(f"Rejected {entry['kb_entry_id']}.")

    def run(self, area: str | None = None, kb_id: str | None = None) -> int:
        try:
            return self._run(area, kb_id)
        except EOFError:
            self.say("\nStopped. Everything decided so far is saved.")
            return 0

    def _run(self, area: str | None, kb_id: str | None) -> int:
        data, registry = self._load()
        queue = [e["kb_entry_id"] for e in drafts(data, area, kb_id)]
        if not queue:
            self.say("No drafts to review.")
            return 0
        self.say(f"{len(queue)} draft(s) to review.")
        for current_id in queue:
            while True:
                data, registry = self._load()
                try:
                    _, entry = find(data, current_id)
                except ReviewError:
                    break
                if entry.get("status") != "draft":
                    break
                self.say(card(entry, registry))
                choice = self.ask("[a]pprove  [e]dit answer  [r]eject  [s]kip  [q]uit: ").strip().lower()
                if choice == "q":
                    self.say("Stopped. Everything decided so far is saved.")
                    return 0
                if choice == "s":
                    break
                if choice == "a":
                    self._approve(entry)
                elif choice == "e":
                    self._edit(entry)
                elif choice == "r":
                    self._reject(entry)
                else:
                    self.say("Choose a, e, r, s or q.")
        self.say("Done.")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--area", help="only drafts of this area")
    parser.add_argument("--id", dest="kb_id", help="only this draft, e.g. KB-N0101")
    parser.add_argument("--list", action="store_true", help="list the drafts and exit")
    parser.add_argument("--retag", metavar="ID", help="change an approved entry's category and terms")
    parser.add_argument("--edit", metavar="ID", help="change an approved entry's answer and source_url")
    args = parser.parse_args(argv)
    reviewer = Reviewer(kb_new_entries.NEW_ENTRIES_PATH, kb_registry.REGISTRY_PATH, REJECTED_PATH)
    if args.edit:
        try:
            return reviewer.edit_approved(args.edit)
        except KeyboardInterrupt:
            print("\nStopped. Nothing was changed.")
            return 0
    if args.retag:
        try:
            return reviewer.retag(args.retag)
        except KeyboardInterrupt:
            print("\nStopped. Nothing was changed.")
            return 0
    if args.list:
        data, _ = reviewer._load()
        for e in drafts(data, args.area, args.kb_id):
            print(f"{e['kb_entry_id']}  {e.get('area')}  {e.get('intent')}  "
                  f"{'answered' if (e.get('answer') or '').strip() else 'empty'}")
        return 0
    try:
        return reviewer.run(args.area, args.kb_id)
    except KeyboardInterrupt:
        print("\nStopped. Everything decided so far is saved.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
