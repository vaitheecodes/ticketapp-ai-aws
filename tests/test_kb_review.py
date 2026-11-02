"""The KB reviewer (scripts/kb_review.py) and the mappings it registers.

- approve / edit / reject: validated before saving; approve sets status
  "approved", the chosen category and reviewed_at, and registers which
  terms the intent is specific to (data/kb/kb_registry.json).
- The interactive loop never approves without a typed "yes", saves each
  decision atomically, and stops cleanly on end of input.
- topic_terms merges the registry; a KB-only category is accepted by the
  validator but never becomes a classifier category.
- KB-N0140 (the "invalid data provided for api" HAR entry) is live.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from backend import graph, kb_new_entries, kb_registry, kb_retrieval, topic_terms
from backend.ai_service.schemas import Categorization

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("kb_review", ROOT / "scripts" / "kb_review.py")
kb_review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kb_review)

DRAFT = {
    "kb_entry_id": "KB-N0901", "status": "draft", "area": "Databases", "category": "APPLICATION_MONITORING",
    "intent": "zebradb_monitoring", "monitor_type": "ZebraDB", "handling": "answer",
    "questions": ["how do I monitor zebradb", "zebradb monitor no data", "zebradb privileges"],
    "collect": ["ZebraDB version", "Agent version"],
    "answer": "Paraphrased test answer that is long enough for the minimum length check.",
    "source_url": "https://www.site24x7.com/help/database-monitoring/zebradb.html",
}
EMPTY = {**DRAFT, "kb_entry_id": "KB-N0902", "intent": "zebradb_errors", "handling": "escalate_with_info",
         "questions": ["zebradb add error", "cannot add zebradb", "zebradb monitor fails"],
         "answer": "", "source_url": ""}


@pytest.fixture()
def files(tmp_path, monkeypatch):
    entries, registry, rejected = tmp_path / "new.json", tmp_path / "registry.json", tmp_path / "rejected.json"
    entries.write_text(json.dumps({"entries": [copy.deepcopy(DRAFT), copy.deepcopy(EMPTY)]}), encoding="utf-8")
    monkeypatch.setattr(kb_registry, "REGISTRY_PATH", registry)
    topic_terms.reload()
    yield entries, registry, rejected
    topic_terms.reload()


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _entry(path, kb_id):
    return next(e for e in _load(path)["entries"] if e["kb_entry_id"] == kb_id)


def _run(files, answers, **kwargs):
    entries, registry, rejected = files
    script = iter(answers)
    said = []

    def ask(prompt=""):
        said.append(prompt)
        try:
            return next(script)
        except StopIteration:
            raise EOFError from None

    reviewer = kb_review.Reviewer(entries, registry, rejected, ask=ask, say=lambda *a: said.append(" ".join(map(str, a))))
    assert reviewer.run(**kwargs) == 0
    return "\n".join(said)


# ---------------------------------------------------------------------------
# Pure decisions
# ---------------------------------------------------------------------------
def test_approve_sets_status_category_and_registers_terms(files):
    data, registry = kb_review.approve(_load(files[0]), kb_registry.empty(), "KB-N0901",
                                       category="APPLICATION_MONITORING", specific_to=["server"],
                                       reviewed_at="2026-11-02T08:00:00+00:00")
    entry = next(e for e in data["entries"] if e["kb_entry_id"] == "KB-N0901")
    assert (entry["status"], entry["category"], entry["reviewed_at"]) == (
        "approved", "APPLICATION_MONITORING", "2026-11-02T08:00:00+00:00")
    assert registry["intent_terms"] == {"zebradb_monitoring": ["server"]}


def test_approve_with_a_new_category_and_a_new_term(files):
    data, registry = kb_review.approve(_load(files[0]), kb_registry.empty(), "KB-N0901",
                                       category="DATABASES", new_category=True, specific_to=["ZebraDB"],
                                       new_terms={"ZebraDB": r"\bzebra ?db\b"})
    assert registry["categories"] == ["DATABASES"] and registry["terms"] == {"ZebraDB": r"\bzebra ?db\b"}
    assert next(e for e in data["entries"] if e["kb_entry_id"] == "KB-N0901")["category"] == "DATABASES"


@pytest.mark.parametrize("kwargs, message", [
    ({"category": "NOPE", "specific_to": []}, "unknown category"),
    ({"category": "bad name", "specific_to": [], "new_category": True}, "UPPER_SNAKE_CASE"),
    ({"category": "APPLICATION_MONITORING", "specific_to": ["Not A Term"]}, "unknown term"),
    ({"category": "APPLICATION_MONITORING", "specific_to": [], "new_terms": {"Bad": "("}}, "invalid regex"),
])
def test_approve_refuses_bad_choices(files, kwargs, message):
    with pytest.raises(kb_review.ReviewError, match=message):
        kb_review.approve(_load(files[0]), kb_registry.empty(), "KB-N0901", **kwargs)


def test_approve_refuses_an_empty_answer_or_an_invalid_entry(files):
    with pytest.raises(kb_review.ReviewError, match="no answer"):
        kb_review.approve(_load(files[0]), kb_registry.empty(), "KB-N0902",
                          category="APPLICATION_MONITORING", specific_to=[])
    data = _load(files[0])
    data["entries"][0]["source_url"] = "https://example.com/not-official"
    with pytest.raises(kb_review.ReviewError, match="official Site24x7 page"):
        kb_review.approve(data, kb_registry.empty(), "KB-N0901", category="APPLICATION_MONITORING", specific_to=[])


def test_edit_validates_and_reject_moves_the_entry(files):
    data = kb_review.edit(_load(files[0]), kb_registry.empty(), "KB-N0902",
                          "A written answer that is long enough to pass the length check.",
                          "https://www.site24x7.com/help/database-monitoring/zebradb.html")
    assert next(e for e in data["entries"] if e["kb_entry_id"] == "KB-N0902")["answer"].startswith("A written")
    with pytest.raises(kb_review.ReviewError, match="needs a source_url"):
        kb_review.edit(_load(files[0]), kb_registry.empty(), "KB-N0902",
                       "A written answer that is long enough to pass the length check.")
    data, rejected = kb_review.reject(_load(files[0]), {"entries": []}, "KB-N0901", "duplicate",
                                      rejected_at="2026-11-02T08:00:00+00:00")
    assert [e["kb_entry_id"] for e in data["entries"]] == ["KB-N0902"]
    assert rejected["entries"][0] | {} == {**DRAFT, "status": "rejected", "rejected_at": "2026-11-02T08:00:00+00:00",
                                           "reject_reason": "duplicate"}


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------
def test_approve_needs_a_typed_yes(files):
    out = _run(files, ["a", "", "server", "y", "s", "q"])
    assert "Not approved." in out
    assert _entry(files[0], "KB-N0901")["status"] == "draft"
    assert not files[1].exists()


def test_interactive_approve_saves_entry_and_registry(files):
    out = _run(files, ["a", "", "server", "yes", "q"])
    assert "Approved KB-N0901 and saved." in out
    entry = _entry(files[0], "KB-N0901")
    assert entry["status"] == "approved" and entry["reviewed_at"]
    assert _load(files[1])["intent_terms"] == {"zebradb_monitoring": ["server"]}
    assert not list(files[0].parent.glob(".*.tmp"))  # atomic writes leave no temp files
    for label in ("area", "category", "intent", "questions", "answer", "source_url", "collect"):
        assert label in out


def test_interactive_new_category_and_generic_terms(files):
    _run(files, ["a", "new", "DATABASES", "y", "none", "yes", "q"])
    assert _entry(files[0], "KB-N0901")["category"] == "DATABASES"
    assert _load(files[1]) == {"categories": ["DATABASES"], "terms": {}, "intent_terms": {"zebradb_monitoring": []}}


def test_an_empty_draft_cannot_be_approved_until_edited(files):
    out = _run(files, ["a", "e",
                       "A written answer that is long enough to pass the length check.", ".",
                       "https://www.site24x7.com/help/database-monitoring/zebradb.html",
                       "s"], kb_id="KB-N0902")
    assert "no answer: edit it before approving" in out and "Answer saved (still a draft)." in out
    entry = _entry(files[0], "KB-N0902")
    assert entry["status"] == "draft" and entry["answer"].startswith("A written")


def test_interactive_reject(files):
    _run(files, ["r", "not accurate", "y", "q"])
    assert [e["kb_entry_id"] for e in _load(files[0])["entries"]] == ["KB-N0902"]
    assert _load(files[2])["entries"][0]["reject_reason"] == "not accurate"


def test_end_of_input_stops_without_changes(files):
    before = files[0].read_text(encoding="utf-8")
    out = _run(files, [])
    assert "Stopped" in out and files[0].read_text(encoding="utf-8") == before


def test_filters_by_area_and_id(files):
    assert "No drafts to review." in _run(files, [], area="Cloud")
    out = _run(files, ["s"], kb_id="KB-N0902")
    assert "KB-N0902" in out and "KB-N0901" not in out


# ---------------------------------------------------------------------------
# Registry in the specificity check and the validator
# ---------------------------------------------------------------------------
def test_topic_terms_use_the_registry(files):
    kb_registry.save({"categories": [], "terms": {"ZebraDB": r"\bzebra ?db\b"},
                      "intent_terms": {"zebradb_monitoring": ["ZebraDB"], "api_monitoring": []}}, files[1])
    topic_terms.reload()
    assert "ZebraDB" in topic_terms.mentioned("our zebra db is slow")
    assert topic_terms.entry_terms("zebradb_monitoring") == {"ZebraDB"}
    assert topic_terms.entry_terms("api_monitoring") == set()  # registry overrides the built-in map
    assert topic_terms.missing_terms({"intent": "zebradb_monitoring", "response": ""}, "my database") == {"ZebraDB"}


def test_a_broken_registry_is_ignored(files):
    files[1].write_text('{"terms": {"Bad": "("}}', encoding="utf-8")
    topic_terms.reload()
    assert topic_terms.entry_terms("api_monitoring") == {"API"}  # built-ins still work


def test_validator_accepts_registered_categories_only():
    data = {"entries": [{**DRAFT, "category": "DATABASES"}]}
    assert any("category must be one of" in e for e in kb_new_entries.validate(data, [], kb_registry.empty()).errors)
    report = kb_new_entries.validate(data, [], {**kb_registry.empty(), "categories": ["DATABASES"]})
    assert report.errors == [] and report.drafts


def test_a_kb_only_category_never_becomes_the_ticket_category(monkeypatch):
    monkeypatch.setattr(graph.categorize_op, "categorize_ticket",
                        lambda data: Categorization(category="MONITORING", confidence=0.3))
    state = {"ticket_text": "Customer: zebradb slow",
             "kb_candidates": [{"kb_entry_id": "KB-N0901", "category": "DATABASES", "intent": "zebradb_monitoring",
                                "instruction": "zebradb slow", "response": "x", "score": 0.95}]}
    assert graph.classify_ticket(state)["category"] == "MONITORING"


# ---------------------------------------------------------------------------
# KB-N0140 is live
# ---------------------------------------------------------------------------
def test_the_har_entry_is_approved_and_live():
    data = json.loads(kb_new_entries.NEW_ENTRIES_PATH.read_text(encoding="utf-8"))
    entry = next(e for e in data["entries"] if e["kb_entry_id"] == "KB-N0140")
    assert entry["status"] == "approved" and entry["reviewed_at"]
    assert json.loads(kb_registry.REGISTRY_PATH.read_text(encoding="utf-8"))["intent_terms"][
        "add_api_monitor_error_har"] == ["API"]
    kb_retrieval._load_index.cache_clear()
    topic_terms.reload()
    try:
        hits = kb_retrieval.retrieve_candidates("invalid data provided for api")
        assert hits and hits[0].entry.kb_entry_id == "KB-N0140"
        state = {"ticket_text": "Customer: invalid data provided for api", "kb_candidates": graph.kb_candidates_for(
            "invalid data provided for api")}
        usable, unconfirmed = graph.grounding_candidates(state)
        assert [c["kb_entry_id"] for c, _ in unconfirmed] == ["KB-N0140"]  # asks: is it an API monitor?
        state["ticket_text"] = "Customer: my REST API monitor says invalid data provided for api"
        usable, _ = graph.grounding_candidates(state)
        assert usable and usable[0]["kb_entry_id"] == "KB-N0140"
    finally:
        kb_retrieval._load_index.cache_clear()
        topic_terms.reload()
