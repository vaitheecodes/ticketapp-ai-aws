"""Hand-written KB entries (data/kb/new_entries.json, backend/kb_new_entries.py).

- The validator: required fields, id format and uniqueness, category,
  intent, question variants, answer length, leftover EXAMPLE/[placeholder]
  text, and duplicates within the file and against kb_entries.json.
- Retrieval loads only valid, non-example entries; the shipped file has
  just the example, so retrieval is unchanged.
- scripts/validate_kb.py exits 1 on errors, 0 otherwise.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend import kb_new_entries, kb_retrieval

ROOT = Path(__file__).resolve().parent.parent
EXISTING = [
    {"kb_entry_id": "KB-0001", "category": "AGENT", "intent": "agent_installation",
     "instruction": "how do I install the Site24x7 agent",
     "response": "Download the agent installer from the Admin page, run it on the server with administrator "
                 "rights and enter the device key. The server appears under Server Monitors within minutes."},
]
GOOD = {
    "kb_entry_id": "KB-N0002", "status": "approved", "category": "AGENT", "intent": "agent_installation",
    "questions": ["my agent stopped reporting", "Agent not sending data", "agent offline"],
    "answer": "Test answer text that is long enough to pass the minimum length check.",
    "source_url": "https://www.site24x7.com/help/admin/adding-a-monitor/linux-server-monitoring.html",
}


def _validate(*entries):
    return kb_new_entries.validate({"entries": list(entries)}, EXISTING)


def _with(**changes):
    entry = copy.deepcopy(GOOD)
    for key, value in changes.items():
        if value is None:
            entry.pop(key)
        else:
            entry[key] = value
    return entry


def test_a_valid_entry_is_flattened_per_question():
    report = _validate(GOOD)
    assert report.errors == [] and report.warnings == []
    assert [e["instruction"] for e in report.entries] == GOOD["questions"]
    assert {e["kb_entry_id"] for e in report.entries} == {"KB-N0002"}
    assert all(e["response"] == GOOD["answer"] for e in report.entries)


@pytest.mark.parametrize("changes, problem", [
    ({"kb_entry_id": None}, "missing required field 'kb_entry_id'"),
    ({"answer": None}, "an approved entry needs an answer"),
    ({"status": None}, "missing required field 'status'"),
    ({"status": "published"}, 'status must be "draft" or "approved"'),
    ({"questions": None}, "missing required field 'questions'"),
    ({"kb_entry_id": "KB-0002"}, 'kb_entry_id must look like "KB-N0001"'),
    ({"kb_entry_id": "KB-N12"}, 'kb_entry_id must look like "KB-N0001"'),
    ({"category": "BILLING"}, "category must be one of"),
    ({"intent": "Agent Offline"}, "intent must be lower_snake_case"),
    ({"questions": []}, "questions must be a non-empty list"),
    ({"questions": "agent offline"}, "questions must be a non-empty list"),
    ({"questions": ["agent offline", "  "]}, "question 2 is empty"),
    ({"questions": ["agent offline", "Agent offline please!"]}, "question 2 repeats another question"),
    ({"answer": "Too short."}, "at least 40 characters"),
    ({"answer": "EXAMPLE: write the support answer here, long enough to pass."}, "EXAMPLE"),
    ({"answer": "Restart the agent and send the log to [Support Email] so we can check."}, "[placeholder]"),
    ({"questions": ["EXAMPLE: a question", "agent offline"]}, "a question still contains"),
    ({"example": "yes"}, '"example" must be true or false'),
])
def test_invalid_entries_are_reported_and_skipped(changes, problem):
    report = _validate(_with(**changes))
    assert report.entries == []
    assert any(problem in e for e in report.errors), report.errors


def test_id_already_in_the_kb_or_used_twice_is_an_error():
    existing_id = copy.deepcopy(EXISTING[0])
    existing_id["kb_entry_id"] = "KB-N0002"
    report = kb_new_entries.validate({"entries": [GOOD]}, EXISTING + [existing_id])
    assert any("already used in kb_entries.json" in e for e in report.errors)

    second = _with(questions=["a different question about agents"])
    report = _validate(GOOD, second)
    assert any("used by another entry in this file" in e for e in report.errors)
    assert {e["kb_entry_id"] for e in report.entries} == {"KB-N0002"}


def test_a_question_shared_by_two_entries_is_an_error():
    second = _with(kb_entry_id="KB-N0003", questions=["Agent not sending data", "agent is silent"])
    report = _validate(GOOD, second)
    assert any("question 1 is also a question of KB-N0002" in e for e in report.errors)


def test_warnings_do_not_block_an_entry():
    entry = _with(intent="agent_upgrade", questions=["how do I install the Site24x7 agent please"],
                  answer=EXISTING[0]["response"], extra="x")
    entry["intent"] = "agent_installation"
    report = _validate(entry)
    assert report.errors == []
    text = " ".join(report.warnings)
    assert "already in kb_entries.json (KB-0001)" in text
    assert "nearly the same as KB-0001" in text
    assert "1 question(s)" in text and "unknown field 'extra'" in text
    assert report.entries


def test_a_new_intent_is_only_a_warning():
    report = _validate(_with(intent="agent_upgrade"))
    assert report.errors == [] and report.entries
    assert any("intent 'agent_upgrade' is new for AGENT" in w for w in report.warnings)


def test_examples_are_checked_but_never_used():
    example = _with(example=True, answer="EXAMPLE: write the support answer here, long enough.",
                    questions=["EXAMPLE: a question"])
    report = _validate(example)
    assert report.errors == [] and report.entries == [] and report.examples == 1


def test_errors_never_quote_the_answer():
    secretish = "Answer-text-that-must-not-be-echoed [Customer Name] padding to forty chars"
    report = _validate(_with(answer=secretish))
    assert report.errors and not any("must-not-be-echoed" in e for e in report.errors)


@pytest.mark.parametrize("data", [[], {"entries": "x"}, {"rows": []}])
def test_a_badly_shaped_file_is_one_error(data):
    report = kb_new_entries.validate(data, EXISTING)
    assert report.errors == ['file: expected an object with an "entries" list'] and report.entries == []


def test_unreadable_json_is_an_error(tmp_path):
    path = tmp_path / "new_entries.json"
    path.write_text("{not json", encoding="utf-8")
    report = kb_new_entries.load(EXISTING, path)
    assert report.entries == [] and "not valid JSON" in report.errors[0]


def test_missing_file_is_no_entries(tmp_path):
    report = kb_new_entries.load(EXISTING, tmp_path / "absent.json")
    assert report.entries == [] and report.errors == []


# ---------------------------------------------------------------------------
# The shipped file and retrieval
# ---------------------------------------------------------------------------
def test_shipped_file_holds_the_example_drafts_and_reviewed_entries():
    """One example, drafts awaiting review, and entries a person approved
    (reviewed_at set). Every answer cites an official page."""
    data = json.loads(kb_new_entries.NEW_ENTRIES_PATH.read_text(encoding="utf-8"))
    existing = json.loads(kb_retrieval.KB_PATH.read_text(encoding="utf-8"))["entries"]
    report = kb_new_entries.validate(data, existing)
    assert report.errors == [] and report.examples == 1
    statuses = [e.get("status") for e in data["entries"] if not e.get("example")]
    assert set(statuses) <= {"draft", "approved"} and statuses.count("draft") >= 30
    assert len(report.drafts) == statuses.count("draft")
    approved = [e for e in data["entries"] if e.get("status") == "approved"]
    assert all(e.get("reviewed_at") for e in approved)
    assert {e["kb_entry_id"] for e in report.entries} == {e["kb_entry_id"] for e in approved}
    for e in data["entries"]:
        if e.get("example"):
            continue
        assert e["area"] and e["collect"] and e["handling"] in kb_new_entries.HANDLING
        if e["status"] == "approved":
            continue
        if e["answer"]:
            assert kb_new_entries.official_source(e["source_url"]), e["kb_entry_id"]
            assert len(e["answer"]) <= 600, f"{e['kb_entry_id']}: keep draft answers short"
        else:
            assert e["handling"] == "escalate_with_info", e["kb_entry_id"]


def test_coverage_doc_lists_every_draft():
    doc = (ROOT / "docs" / "KB_COVERAGE.md").read_text(encoding="utf-8")
    data = json.loads(kb_new_entries.NEW_ENTRIES_PATH.read_text(encoding="utf-8"))
    for e in data["entries"]:
        if e.get("status") == "draft":
            assert e["kb_entry_id"] in doc


def test_retrieval_loads_valid_entries_and_skips_examples_and_errors(monkeypatch, tmp_path):
    path = tmp_path / "new_entries.json"
    entry = _with(kb_entry_id="KB-N0042", category="SNMP", intent="snmp_monitoring",
                  questions=["zebracorn switch flaps", "zebracorn uplink flapping", "zebracorn port flap"])
    broken = _with(kb_entry_id="KB-N0043", questions=["quokkatron flux"], answer="short")
    example = _with(kb_entry_id="KB-N0044", example=True, questions=["wombatize the collector"])
    path.write_text(json.dumps({"entries": [entry, broken, example]}), encoding="utf-8")
    monkeypatch.setattr(kb_new_entries, "NEW_ENTRIES_PATH", path)
    kb_retrieval._load_index.cache_clear()
    try:
        hits = kb_retrieval.retrieve_candidates("zebracorn switch flaps")
        assert hits and hits[0].entry.kb_entry_id == "KB-N0042"
        assert kb_retrieval.retrieve_candidates("quokkatron flux") == []
        assert kb_retrieval.retrieve_candidates("wombatize the collector") == []
    finally:
        kb_retrieval._load_index.cache_clear()


def test_validator_script_exit_codes(tmp_path):
    script = [sys.executable, str(ROOT / "scripts" / "validate_kb.py")]
    shipped = subprocess.run(script, capture_output=True, text=True, cwd=ROOT)
    assert shipped.returncode == 0, shipped.stdout + shipped.stderr
    assert "1 example(s) skipped" in shipped.stdout and "approved and usable" in shipped.stdout
    assert "drafts by area" in shipped.stdout and "Databases:" in shipped.stdout

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"entries": [_with(category="BILLING")]}), encoding="utf-8")
    result = subprocess.run(script + [str(bad)], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 1 and "ERROR" in result.stdout and "category must be one of" in result.stdout


# ---------------------------------------------------------------------------
# Drafts: never used; a draft answer needs an official source
# ---------------------------------------------------------------------------
DRAFT = {
    "kb_entry_id": "KB-N0050", "status": "draft", "area": "Databases", "category": "APPLICATION_MONITORING",
    "intent": "mysql_monitoring", "monitor_type": "MySQL", "handling": "answer",
    "questions": ["how do I monitor mysql", "mysql monitor no data", "mysql privileges for monitoring"],
    "collect": ["MySQL version", "Agent version"],
    "answer": "Paraphrased test answer that is long enough for the minimum length.",
    "source_url": "https://www.site24x7.com/help/database-monitoring/mysql-monitor.html",
}


def _draft(**changes):
    entry = copy.deepcopy(DRAFT)
    for key, value in changes.items():
        if value is None:
            entry.pop(key)
        else:
            entry[key] = value
    return entry


def test_a_draft_is_reported_but_never_usable():
    report = _validate(_draft(), _draft(kb_entry_id="KB-N0051", answer="", source_url="",
                                        handling="escalate_with_info", area="Add-monitor errors",
                                        questions=["error adding a database monitor"]))
    assert report.errors == [] and report.entries == []
    assert [(d["kb_entry_id"], d["area"], d["has_answer"], d["source_url"] is not None) for d in report.drafts] == [
        ("KB-N0050", "Databases", True, True), ("KB-N0051", "Add-monitor errors", False, False)]


@pytest.mark.parametrize("changes, problem", [
    ({"source_url": None}, "a draft with an answer needs a source_url"),
    ({"source_url": ""}, "a draft with an answer needs a source_url"),
    ({"source_url": "http://www.site24x7.com/help/x.html"}, "official Site24x7 page"),
    ({"source_url": "https://example.com/help/x.html"}, "official Site24x7 page"),
    ({"source_url": "https://www.site24x7.com/"}, "official Site24x7 page"),
    ({"area": None}, "a draft needs an area"),
    ({"handling": "maybe"}, 'handling must be "answer" or "escalate_with_info"'),
    ({"collect": []}, "collect must be a non-empty list"),
    ({"collect": ["ok", " "]}, "collect must be a non-empty list"),
    ({"answer": "too short"}, "at least 40 characters"),
])
def test_invalid_drafts_are_errors(changes, problem):
    report = _validate(_draft(**changes))
    assert any(problem in e for e in report.errors), report.errors
    assert report.drafts == []


def test_a_draft_without_an_answer_needs_no_source():
    report = _validate(_draft(answer="", source_url=None))
    assert report.errors == [] and report.drafts[0]["has_answer"] is False


def test_support_kb_is_an_official_source():
    assert kb_new_entries.official_source("https://support.site24x7.com/portal/en/kb/articles/x")
    assert not kb_new_entries.official_source("https://support.site24x7.com.evil.example/x")


def test_an_approved_entry_without_a_source_gets_a_warning():
    report = _validate(_with(source_url=None))
    assert report.errors == [] and report.entries
    assert any("no source_url" in w for w in report.warnings)


def test_retrieval_never_loads_drafts(monkeypatch, tmp_path):
    path = tmp_path / "new_entries.json"
    path.write_text(json.dumps({"entries": [
        _draft(questions=["zebracorn database flaps", "zebracorn db flapping", "zebracorn db down"]),
        _draft(kb_entry_id="KB-N0052", status="approved",
               questions=["quokkatron replication lag", "quokkatron lag", "quokkatron replica slow"]),
    ]}), encoding="utf-8")
    monkeypatch.setattr(kb_new_entries, "NEW_ENTRIES_PATH", path)
    kb_retrieval._load_index.cache_clear()
    try:
        assert kb_retrieval.retrieve_candidates("zebracorn database flaps") == []
        hits = kb_retrieval.retrieve_candidates("quokkatron replication lag")
        assert hits and hits[0].entry.kb_entry_id == "KB-N0052"
    finally:
        kb_retrieval._load_index.cache_clear()
