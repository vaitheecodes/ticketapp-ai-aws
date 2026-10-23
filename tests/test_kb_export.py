"""The runtime KB file (data/kb/kb_entries.json, loaded without pandas)
must match the silver Parquet tables it was exported from, and retrieval
over it must return exactly what retrieval over the Parquet did.

Skipped if the pipeline hasn't produced data/silver/ (or pandas isn't
installed, e.g. a runtime-only environment)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from backend import kb_retrieval

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("export_kb", ROOT / "scripts" / "export_kb.py")
export_kb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_kb)

pytest.importorskip("pandas")
if not (export_kb.TICKETS.exists() and export_kb.RESOLUTIONS.exists()):
    pytest.skip("silver tables not generated", allow_module_level=True)

QUERIES = [
    "My Linux server monitor shows no data since 10:00",
    "agent service stopped on windows server",
    "How do I set up an alert when CPU goes above 90%?",
    "add a new website monitor",
    "please downgrade my plan and refund the invoice",
    "delete my old monitoring data",
    "integrate alerts with slack",
    "SSL certificate expiry alert",
    "kubernetes cluster monitoring setup",
    "it is not working",
    "log management search query",
    "how many monitors does my license allow",
    "network device snmp traps not received",
    "generate a monthly availability report",
    "APM insight java agent installation",
    "thanks",
    "",
]


@pytest.fixture(scope="module")
def parquet_entries():
    return export_kb.entries_from_parquet()


def test_runtime_file_is_current(parquet_entries):
    on_disk = json.loads(kb_retrieval.KB_PATH.read_text(encoding="utf-8"))
    assert on_disk["count"] == len(parquet_entries) == 386
    assert on_disk["entries"] == parquet_entries, "re-run scripts/export_kb.py"


@pytest.mark.parametrize("query", QUERIES)
def test_retrieval_identical_to_parquet(parquet_entries, query):
    before = kb_retrieval.build_index(parquet_entries).search(query, top_k=5)
    after = kb_retrieval._load_index().search(query, top_k=5)
    assert [(c.entry, round(c.score, 12)) for c in before] == [(c.entry, round(c.score, 12)) for c in after]
