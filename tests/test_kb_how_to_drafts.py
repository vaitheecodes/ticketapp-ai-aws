"""The how-to drafts in data/kb/new_entries.json ("Getting started
(how-to)") and the how-to retrieval rule.

- Every draft is shaped for review: handling "answer", kind "how-to",
  numbered steps, an official help page as source_url.
- Approved (in a temporary copy, never in the real file), each draft is
  the first entry a how-to answer may use for its own phrasings, and the
  two questions that got "no article" on the live deployment are grounded.
- `kind` is validated, and it beats the wording guess: a "troubleshooting"
  entry never answers a how-to, a "how-to" entry does even when its
  question has a fault word.
"""

from __future__ import annotations

import json
import re

import pytest

from backend import graph, kb_new_entries, kb_retrieval, request_kind

AREA = "Getting started (how-to)"


def _file() -> dict:
    return json.loads(kb_new_entries.NEW_ENTRIES_PATH.read_text(encoding="utf-8"))


def _drafts() -> list[dict]:
    return [e for e in _file()["entries"] if e.get("area") == AREA]


@pytest.fixture()
def approved(tmp_path, monkeypatch):
    """The real new_entries.json with the how-to drafts approved, in a temp file."""
    data = _file()
    for entry in data["entries"]:
        if entry.get("area") == AREA:
            entry["status"] = "approved"
    path = tmp_path / "new_entries.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(kb_new_entries, "NEW_ENTRIES_PATH", path)
    kb_retrieval._load_index.cache_clear()
    yield
    kb_retrieval._load_index.cache_clear()


def _usable(question: str) -> list[str]:
    """The entries a how-to answer may rely on, best first (the real path:
    retrieval, then graph.grounding_candidates)."""
    text = f"Customer: {question}"
    candidates = graph.kb_candidates_for(graph.retrieval_query(text), skip_faults=graph.is_how_to(text))
    usable, _ = graph.grounding_candidates({"ticket_text": text, "kb_candidates": candidates})
    return [c["kb_entry_id"] for c in usable]


def test_drafts_are_shaped_for_review():
    drafts = _drafts()
    assert len(drafts) >= 12
    report = kb_new_entries.validate({"entries": drafts}, existing=[])
    assert report.errors == []
    for e in drafts:
        assert e["handling"] == "answer" and e["kind"] == "how-to", e["kb_entry_id"]
        assert e["source_url"].startswith("https://www.site24x7.com/help/"), e["kb_entry_id"]
        steps = [line for line in e["answer"].splitlines() if re.match(r"\d+\. ", line)]
        assert len(steps) >= 3 and [int(s.split(".")[0]) for s in steps] == list(range(1, len(steps) + 1))
        assert len(e["questions"]) >= 3


def test_drafts_are_not_used_until_approved(tmp_path, monkeypatch):
    # Every how-to draft is approved in the real file now: put them back
    # to draft in a temporary copy.
    data = _file()
    for entry in data["entries"]:
        if entry.get("area") == AREA:
            entry["status"] = "draft"
    path = tmp_path / "new_entries.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(kb_new_entries, "NEW_ENTRIES_PATH", path)
    kb_retrieval._load_index.cache_clear()
    try:
        loaded = {e.kb_entry_id for e in kb_retrieval._load_index().entries}
    finally:
        kb_retrieval._load_index.cache_clear()
    ids = {e["kb_entry_id"] for e in _drafts()}
    assert ids and not ids & loaded


def test_each_approved_draft_answers_its_own_how_to_phrasings(approved):
    for e in _drafts():
        for q in e["questions"]:
            if request_kind.is_how_to(q):
                assert _usable(q)[:1] == [e["kb_entry_id"]], (q, _usable(q))


@pytest.mark.parametrize("question, entry", [
    ("how to add a monitor in the Linux monitoring agent", "KB-N0141"),  # no KB match on the live deployment
    ("how to add a monitor in the monitoring agent on Windows", "KB-N0142"),
    ("how to generate a report", "KB-N0144"),  # matched report_dashboard (no steps), not answered
    ("I need to add IOS app", "KB-N0150"),
    ("how do I set up alerts", "KB-N0147"),
    ("how to monitor an iPhone app", "KB-N0150"),
    ("how to monitor an iPad app", "KB-N0150"),
])
def test_live_how_to_questions_are_grounded_once_approved(approved, question, entry):
    assert _usable(question)[:1] == [entry]


def test_the_agent_guides_ask_linux_or_windows_first(approved):
    # The agent guides are registered as Linux / Windows specific.
    text = "Customer: how to add a monitor in the monitoring agent"
    candidates = graph.kb_candidates_for(graph.retrieval_query(text), skip_faults=True)
    assert _usable("how to add a monitor in the monitoring agent") == []
    assert graph.how_to_question({"ticket_text": text, "kb_candidates": candidates}) == \
        graph.HOW_TO_VARIANT_QUESTION.format(options="Linux or Windows")


@pytest.mark.parametrize("question, other", [
    ("how to monitor an iPad app", "KB-N0151"),        # the Android guide
    ("how to monitor an android app", "KB-N0150"),     # the iOS guide
    ("How do I install the Site24x7 agent on Windows?", "KB-N0141"),  # the Linux guide
])
def test_a_guide_for_another_variant_never_grounds_the_answer(approved, question, other):
    assert other not in _usable(question)


def _entry(question: str, kind: str | None) -> dict:
    entry = {"kb_entry_id": "KB-N9001", "status": "approved", "category": "MONITORING", "intent": "monitor_status",
             "questions": [question], "answer": "1. Open the zebracorn page.\n2. Click Add Zebracorn.\n3. Save it.",
             "source_url": "https://www.site24x7.com/help/x.html"}
    if kind:
        entry["kind"] = kind
    return entry


def test_kind_is_validated():
    report = kb_new_entries.validate({"entries": [_entry("how do I add a zebracorn", "recipe")]}, existing=[])
    assert any('kind must be "how-to" or "troubleshooting"' in err for err in report.errors)
    report = kb_new_entries.validate({"entries": [_entry("how do I add a zebracorn", "how-to")]}, existing=[])
    assert report.errors == [] and report.entries[0]["kind"] == "how-to"


@pytest.mark.parametrize("question, kind, answers", [
    ("how do I add a zebracorn monitor", "troubleshooting", False),  # kind beats the wording
    ("zebracorn monitor missing from my list: add it", "how-to", True),  # "missing" is a fault word
    ("how do I add a zebracorn monitor", None, True),  # no kind: the wording decides
])
def test_kind_beats_the_wording_guess(tmp_path, monkeypatch, question, kind, answers):
    path = tmp_path / "new_entries.json"
    path.write_text(json.dumps({"entries": [_entry(question, kind)]}), encoding="utf-8")
    monkeypatch.setattr(kb_new_entries, "NEW_ENTRIES_PATH", path)
    kb_retrieval._load_index.cache_clear()
    try:
        assert ("KB-N9001" in _usable("how do I add a zebracorn monitor")) is answers
    finally:
        kb_retrieval._load_index.cache_clear()
