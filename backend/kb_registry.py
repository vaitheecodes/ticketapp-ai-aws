"""KB mappings added while reviewing entries (scripts/kb_review.py):
data/kb/kb_registry.json.

- categories: extra categories for KB entries, beyond the classifier's 17
  (ai_service.schemas.CATEGORIES; repeating one of them, in any case, is
  invalid). They label KB entries only: the
  classifier never returns them and routing never uses them, until they
  are added to the code (schemas.CATEGORIES, departments, frontend).
- terms: extra specific terms (label -> regex matched case-insensitively
  against the customer's own words), beyond topic_terms.TERMS. A regex
  must be non-empty and must not match an empty message (it would count
  as "mentioned" in every message). A label that repeats a built-in term
  is skipped by topic_terms (the built-in pattern stays).
- intent_terms: which terms an approved entry's intent depends on
  (intent -> [labels]); an empty list registers the intent as generic.
  backend/topic_terms.py merges these with its built-in INTENT_TERMS.

Written atomically; a broken file is ignored (with a warning) rather than
breaking retrieval.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "kb" / "kb_registry.json"
CATEGORY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,40}$")
INTENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ()/+.-]{0,40}$")


def empty() -> dict:
    return {"categories": [], "terms": {}, "intent_terms": {}}


def regex_problem(pattern) -> str | None:
    """Why `pattern` can't be a term's regex (None: it can)."""
    if not isinstance(pattern, str) or not pattern.strip():
        return "is empty"
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return "is not a valid regex"
    if compiled.search(""):
        return "matches an empty message, so it would match every message"
    return None


def problems(data) -> list[str]:
    """What is wrong with a registry (empty list: valid)."""
    from backend.ai_service.schemas import CATEGORIES  # lazy: no import cycle

    if not isinstance(data, dict):
        return ["registry must be a JSON object"]
    out = []
    categories = data.get("categories", [])
    if not isinstance(categories, list) or not all(isinstance(c, str) and CATEGORY_PATTERN.match(c)
                                                    for c in categories):
        out.append("categories must be a list of UPPER_SNAKE_CASE names")
    else:
        seen: set[str] = set()
        for name in categories:
            if name.upper() in CATEGORIES:
                out.append(f"category {name} is already one of the 17 categories")
            elif name.upper() in seen:
                out.append(f"category {name} is listed twice")
            seen.add(name.upper())
    terms = data.get("terms", {})
    if not isinstance(terms, dict):
        out.append("terms must be an object of label -> regex")
    else:
        seen = set()
        for label, pattern in terms.items():
            if not LABEL_PATTERN.match(str(label)):
                out.append(f"term label {label!r} is not a short plain label")
            if str(label).lower() in seen:
                out.append(f"term label {label!r} is listed twice")
            seen.add(str(label).lower())
            problem = regex_problem(pattern)
            if problem:
                out.append(f"term {label!r}: the regex {problem}")
    intent_terms = data.get("intent_terms", {})
    if not isinstance(intent_terms, dict):
        out.append("intent_terms must be an object of intent -> [labels]")
    else:
        for intent, labels in intent_terms.items():
            if not INTENT_PATTERN.match(str(intent)):
                out.append(f"intent {intent!r} must be lower_snake_case")
            if not isinstance(labels, list) or not all(isinstance(lab, str) for lab in labels):
                out.append(f"intent {intent!r} must map to a list of labels")
    return out


def load(path: Path | None = None) -> dict:
    path = path or REGISTRY_PATH
    if not path.exists():
        return empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("%s is not valid JSON; ignoring it", path.name)
        return empty()
    found = problems(data)
    if found:
        logger.warning("%s ignored: %s", path.name, "; ".join(found))
        return empty()
    return {**empty(), **data}


def save(data: dict, path: Path | None = None) -> None:
    """Validate, then write atomically (temp file + rename)."""
    found = problems(data)
    if found:
        raise ValueError("; ".join(found))
    path = path or REGISTRY_PATH
    write_json_atomic(path, data)


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
