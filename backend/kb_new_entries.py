"""New knowledge-base entries written by hand: data/kb/new_entries.json.

The format and the writing rules are in docs/KB_ENTRY_TEMPLATE.md;
scripts/validate_kb.py runs `validate` and prints the report. Retrieval
(backend/kb_retrieval.py) loads only entries with status "approved" that
pass, never a "draft" and never an entry marked "example": true.

Drafts may have an empty answer (to be written by a person). A draft
with an answer must cite the official Site24x7 page it was paraphrased
from (source_url). Approved entries need an answer.

`validate` returns the usable (approved) entries, the drafts (for the
report), errors (the entry is skipped) and warnings (the entry is used,
but have a look). Messages name the entry and field; they never quote
the answer text.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from backend import kb_registry
from backend.ai_service.schemas import CATEGORIES

NEW_ENTRIES_PATH = Path(__file__).resolve().parent.parent / "data" / "kb" / "new_entries.json"

ID_PATTERN = re.compile(r"^KB-N\d{4}$")
REQUIRED = ("kb_entry_id", "category", "intent", "questions", "status")
OPTIONAL = ("example", "notes", "answer", "source_url", "area", "monitor_type", "handling", "kind", "collect",
            "reviewed_at", "retagged_at", "edited_at")
STATUSES = ("draft", "approved")
HANDLING = ("answer", "escalate_with_info")
# "how-to": doing a task (add, set up, configure); "troubleshooting": fixing
# a fault. Overrides the question-wording guess (request_kind) in retrieval.
KINDS = ("how-to", "troubleshooting")
# Official Site24x7 documentation: the help site and the support knowledge base.
SOURCE_HOSTS = ("www.site24x7.com", "site24x7.com", "support.site24x7.com")
MIN_ANSWER_CHARS = 40
RECOMMENDED_QUESTIONS = 3
# Same rule as kb_retrieval.DUPLICATE_ANSWER_SIMILARITY: an answer this
# close to an existing one in the same intent is the same topic.
DUPLICATE_ANSWER_SIMILARITY = 0.85
_POLITE = re.compile(r"\b(please|kindly|pls|plz|thanks|thank you|hi|hello|hey)\b")
_PLACEHOLDER = re.compile(r"\bEXAMPLE\b|\[[A-Za-z][A-Za-z' -]{0,40}\]")


@dataclass
class Report:
    entries: list[dict] = field(default_factory=list)   # usable (approved), flattened per question
    drafts: list[dict] = field(default_factory=list)    # {kb_entry_id, area, handling, has_answer, source_url}
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    examples: int = 0
    checked: int = 0


def normalise_question(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", _POLITE.sub(" ", text.lower())))


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def official_source(url) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    return parsed.scheme == "https" and parsed.hostname in SOURCE_HOSTS and bool(parsed.path.strip("/"))


def allowed_categories(registry: dict | None = None) -> list[str]:
    """The classifier's 17 categories plus categories added in
    data/kb/kb_registry.json (KB labels only; see backend/kb_registry.py)."""
    registry = registry if registry is not None else kb_registry.load()
    return list(CATEGORIES) + [c for c in registry["categories"] if c not in CATEGORIES]


def validate(data, existing: list[dict], registry: dict | None = None) -> Report:
    """`data`: the parsed new_entries.json. `existing`: the entries of
    data/kb/kb_entries.json (kb_entry_id, category, intent, instruction,
    response). `registry`: kb_registry contents (default: the file)."""
    report = Report()
    categories = allowed_categories(registry)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        report.errors.append('file: expected an object with an "entries" list')
        return report

    known_intents: dict[str, set[str]] = {}
    for e in existing:
        known_intents.setdefault(e["category"], set()).add(e["intent"])
    existing_ids = {e["kb_entry_id"] for e in existing}
    existing_questions = {normalise_question(e["instruction"]): e["kb_entry_id"] for e in existing}
    seen_ids: set[str] = set()
    seen_questions: dict[str, str] = {}

    for n, entry in enumerate(data["entries"], start=1):
        label = f"entry {n}"
        if not isinstance(entry, dict):
            report.errors.append(f"{label}: expected an object")
            continue
        kb_id = entry.get("kb_entry_id")
        if isinstance(kb_id, str) and kb_id:
            label = f"entry {n} ({kb_id})"
        example = entry.get("example") is True
        status = entry.get("status")
        report.checked += 1
        problems: list[str] = []

        for name in REQUIRED:
            if name not in entry and not (example and name == "status"):
                problems.append(f"missing required field {name!r}")
        for name in entry:
            if name not in REQUIRED + OPTIONAL:
                report.warnings.append(f"{label}: unknown field {name!r} is ignored")
        if "example" in entry and not isinstance(entry["example"], bool):
            problems.append('"example" must be true or false')
        if "status" in entry and status not in STATUSES:
            problems.append('status must be "draft" or "approved"')
        draft = status == "draft"

        if "kb_entry_id" in entry:
            if not isinstance(kb_id, str) or not ID_PATTERN.match(kb_id):
                problems.append('kb_entry_id must look like "KB-N0001" (KB-N plus 4 digits)')
            elif kb_id in existing_ids:
                problems.append("kb_entry_id is already used in kb_entries.json")
            elif kb_id in seen_ids:
                problems.append("kb_entry_id is used by another entry in this file")
            else:
                seen_ids.add(kb_id)

        category, intent = entry.get("category"), entry.get("intent")
        if "category" in entry and category not in categories:
            problems.append(f"category must be one of: {', '.join(categories)}")
        if "intent" in entry:
            if not isinstance(intent, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", intent):
                problems.append("intent must be lower_snake_case (e.g. agent_installation)")
            elif category in categories and intent not in known_intents.get(category, set()) and not draft:
                known = ", ".join(sorted(known_intents.get(category, set()))) or "none yet"
                report.warnings.append(f"{label}: intent {intent!r} is new for {category} "
                                       f"(existing: {known}); fine if intended")

        for name in ("area", "monitor_type"):
            if name in entry and (not isinstance(entry[name], str) or not entry[name].strip()):
                problems.append(f"{name} must be non-empty text")
        if draft and not str(entry.get("area") or "").strip():
            problems.append("a draft needs an area (used to group the drafts report)")
        if "handling" in entry and entry["handling"] not in HANDLING:
            problems.append('handling must be "answer" or "escalate_with_info"')
        if "kind" in entry and entry["kind"] not in KINDS:
            problems.append('kind must be "how-to" or "troubleshooting"')
        if "collect" in entry and (not isinstance(entry["collect"], list) or not entry["collect"]
                                   or not all(isinstance(c, str) and c.strip() for c in entry["collect"])):
            problems.append("collect must be a non-empty list of text items")

        questions = entry.get("questions")
        clean_questions: list[str] = []
        if "questions" in entry:
            if not isinstance(questions, list) or not questions:
                problems.append("questions must be a non-empty list of strings")
            else:
                for i, q in enumerate(questions, start=1):
                    if not isinstance(q, str) or not q.strip():
                        problems.append(f"question {i} is empty or not text")
                        continue
                    key = normalise_question(q)
                    if not key:
                        problems.append(f"question {i} has no words")
                    elif key in {normalise_question(c) for c in clean_questions}:
                        problems.append(f"question {i} repeats another question of this entry")
                    elif not example and key in seen_questions:
                        problems.append(f"question {i} is also a question of {seen_questions[key]}")
                    else:
                        if not example and key in existing_questions:
                            report.warnings.append(f"{label}: question {i} is already in kb_entries.json "
                                                   f"({existing_questions[key]})")
                        clean_questions.append(q.strip())
                if 0 < len(questions) < RECOMMENDED_QUESTIONS:
                    report.warnings.append(f"{label}: {len(questions)} question(s); "
                                           f"{RECOMMENDED_QUESTIONS}+ phrasings retrieve better")

        answer = entry.get("answer")
        has_answer = isinstance(answer, str) and bool(answer.strip())
        if "answer" in entry and not isinstance(answer, str):
            problems.append("answer must be text")
        elif status == "approved" and not has_answer:
            problems.append("an approved entry needs an answer")
        if has_answer:
            if len(answer.strip()) < MIN_ANSWER_CHARS:
                problems.append(f"answer must be at least {MIN_ANSWER_CHARS} characters")
            elif not example:
                if _PLACEHOLDER.search(answer):
                    problems.append('answer still contains "EXAMPLE" or [placeholder] text')
                for e in existing:
                    if (e["category"], e["intent"]) == (category, intent) and difflib.SequenceMatcher(
                            None, _words(e["response"]), _words(answer), autojunk=False
                    ).ratio() >= DUPLICATE_ANSWER_SIMILARITY:
                        report.warnings.append(f"{label}: answer is nearly the same as {e['kb_entry_id']}'s; "
                                               "it will be treated as the same topic")
                        break
        source = entry.get("source_url")
        if source not in (None, "") and not official_source(source):
            problems.append("source_url must be an https link to an official Site24x7 page "
                            f"({', '.join(SOURCE_HOSTS)})")
        elif draft and has_answer and not source:
            problems.append("a draft with an answer needs a source_url (the official page it paraphrases)")
        elif status == "approved" and not source:
            report.warnings.append(f"{label}: no source_url; record where the answer comes from")
        if not example and any(_PLACEHOLDER.search(q) for q in clean_questions):
            problems.append('a question still contains "EXAMPLE" or [placeholder] text')

        if problems:
            report.errors.extend(f"{label}: {p}" for p in problems)
            continue
        if example:
            report.examples += 1
            continue
        for q in clean_questions:
            seen_questions.setdefault(normalise_question(q), kb_id)
        if draft:
            report.drafts.append({"kb_entry_id": kb_id, "area": entry["area"].strip(),
                                  "handling": entry.get("handling"), "has_answer": has_answer,
                                  "source_url": source or None})
            continue
        for q in clean_questions:
            report.entries.append({"kb_entry_id": kb_id, "category": category, "intent": intent,
                                   "instruction": q, "response": answer.strip(),
                                   "handling": entry.get("handling") or "answer",
                                   "kind": entry.get("kind"),
                                   "collect": list(entry.get("collect") or [])})
    return report


def load(existing: list[dict], path: Path | None = None) -> Report:
    """The validated new entries; an absent file is simply no entries."""
    path = path or NEW_ENTRIES_PATH
    if not path.exists():
        return Report()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Report(errors=[f"file: not valid JSON ({type(exc).__name__}: line "
                              f"{getattr(exc, 'lineno', '?')})"])
    return validate(data, existing)
