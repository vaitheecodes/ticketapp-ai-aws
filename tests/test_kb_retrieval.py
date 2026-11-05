"""Tests for backend/kb_retrieval.py's KB-candidate retrieval and for
backend/graph.py's use of it to fix two bugs surfaced by manual testing
of the Site24x7 KB swap:

  1. ask_clarifying_question asked vague, ungrounded questions instead
     of resolving a straightforward request in one turn, because
     classify_ticket had no grounding in the real KB/intent taxonomy.
  2. The order-lookup identity-verification prompt leaked into unrelated
     (monitoring) requests because _ORDER_RELATED_CATEGORIES still
     referenced the old Bitext-era category names.

Requires the pipeline to have been run first (data/silver/*.parquet).
No LLM/network calls — kb_retrieval is pure Python + pandas, and
lookup_order's early-return path needs no DB connection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import db, kb_retrieval  # noqa: E402
from backend.graph import (  # noqa: E402
    _ORDER_RELATED_CATEGORIES,
    _customer_authored_text,
    lookup_order,
    retrieve_kb_candidates,
)


@pytest.fixture(scope="module", autouse=True)
def _require_silver_tables():
    if kb_retrieval._load_index() is None:
        pytest.skip("data/silver/*.parquet not found; run the pipeline first")


@pytest.fixture(scope="module", autouse=True)
def _init_sqlite_db():
    # lookup_order's explicit-order-number path queries the `orders`
    # table; init_db() is idempotent (seed_orders skips if already
    # seeded), so this is safe even if backend/ticketapp.db already
    # exists from running the app locally.
    db.init_db()


# ---------------------------------------------------------------------------
# Bug 1: retrieval must ground short/casual phrasings in a real KB match
# ---------------------------------------------------------------------------
def test_short_casual_phrasing_matches_confidently():
    """The exact phrasing from the bug report: should resolve to a
    WEB_MONITORING/website_monitoring KB entry with a score clearing
    STRONG_MATCH_SCORE, not fall through to a vague clarify loop."""
    candidates = kb_retrieval.retrieve_candidates(
        "I want you to help on the website monitor", top_k=3
    )
    assert candidates, "expected at least one KB candidate"
    top = candidates[0]
    assert top.entry.category == "WEB_MONITORING"
    assert top.entry.intent == "website_monitoring"
    assert top.score >= kb_retrieval.STRONG_MATCH_SCORE


def test_exact_phrasing_scores_near_one():
    candidates = kb_retrieval.retrieve_candidates(
        "how do I install the Site24x7 agent", top_k=1
    )
    assert candidates
    assert candidates[0].entry.category == "AGENT"
    assert candidates[0].score > 0.9


def test_out_of_domain_query_returns_no_strong_match():
    """Bitext-era e-commerce phrasing has no lexical overlap with the
    Site24x7 KB and shouldn't be forced into a false match."""
    candidates = kb_retrieval.retrieve_candidates("refund my order please", top_k=3)
    assert not any(c.score >= kb_retrieval.STRONG_MATCH_SCORE for c in candidates)


def test_filler_words_do_not_dominate_scoring():
    """Regression guard for the specific failure mode found in this bug:
    generic filler words ("want", "team", "go") are rare in the KB's
    formally-phrased instructions and so get an anomalously high
    document-frequency IDF, letting filler-word overlap outrank the
    real topic match unless stopwords are filtered."""
    candidates = kb_retrieval.retrieve_candidates(
        "I want you to help on the website monitor\ni want to configure it",
        top_k=1,
    )
    assert candidates
    assert candidates[0].entry.category == "WEB_MONITORING"


def test_empty_query_returns_no_candidates():
    assert kb_retrieval.retrieve_candidates("", top_k=3) == []


# ---------------------------------------------------------------------------
# Bug 1 (continued): only customer-authored transcript lines feed retrieval
# ---------------------------------------------------------------------------
def test_customer_authored_text_extracts_only_customer_lines():
    transcript = (
        "Customer: I want you to help on the website monitor\n"
        "Agent (AI): Are you asking about contacting support or using the feature?\n"
        "Customer: i want to configure it"
    )
    extracted = _customer_authored_text(transcript)
    assert "Agent (AI)" not in extracted
    assert "website monitor" in extracted
    assert "configure it" in extracted


def test_customer_authored_text_passes_through_plain_ticket_text():
    # /api/tickets/new sends raw ticket_text with no speaker prefixes.
    plain = "I want you to help on the website monitor"
    assert _customer_authored_text(plain) == plain


def test_full_transcript_retrieval_beats_ai_noise():
    """The full (Customer + Agent) transcript, once speaker-filtered,
    should still retrieve the right KB entry even after two rounds of
    the AI's own (generic) clarifying questions were appended."""
    transcript = (
        "Customer: I want you to help on the website monitor\n"
        "Agent (AI): Are you asking about contacting support or using the feature?\n"
        "Customer: i want to configure it\n"
        "Agent (AI): Do you mean configuring it or setting up a new subscription?\n"
        "Customer: yes adding to my account"
    )
    candidates = kb_retrieval.retrieve_candidates(
        _customer_authored_text(transcript), top_k=3
    )
    # The approved "add a user to my account" guide (KB-N0146) matches the
    # customer's own last words; nothing comes from the AI's questions
    # (contacting support, a subscription).
    assert "WEB_MONITORING" in [c.entry.category for c in candidates]
    assert not {c.entry.intent for c in candidates} & {"contact_support", "licensing"}


# ---------------------------------------------------------------------------
# Bug 2: order-lookup must not leak into non-order categories
# ---------------------------------------------------------------------------
def test_no_site24x7_category_is_order_related():
    assert _ORDER_RELATED_CATEGORIES == set()


def test_lookup_order_does_not_fire_for_monitoring_category():
    result = lookup_order(
        {
            "ticket_text": "I want you to help on the website monitor",
            "category": "WEB_MONITORING",
        }
    )
    assert result == {"order_lookup": None}


@pytest.mark.parametrize("category", ["MONITORING", "AGENT", "ALERTS", "INTEGRATION"])
def test_lookup_order_never_fires_for_monitoring_domain_categories(category):
    result = lookup_order(
        {"ticket_text": "some ticket text with no order number", "category": category}
    )
    assert result == {"order_lookup": None}


def test_lookup_order_still_fires_on_explicit_order_number_regardless_of_category():
    # Defensive path: a literal ORD-XXXXX in the text still triggers a
    # lookup even though no category is order-related anymore. Since the
    # order isn't in the mock table, this should resolve to not_found
    # without needing any DB setup beyond the default connection.
    result = lookup_order(
        {
            "ticket_text": "my order ORD-999999 status please",
            "category": "MONITORING",
        }
    )
    assert result["order_lookup"]["not_found"] is True
    assert result["order_lookup"]["order_id"] == "ORD-999999"


# ---------------------------------------------------------------------------
# Near-duplicate KB entries collapse into one topic
# ---------------------------------------------------------------------------
def _entry(kb_id, question, answer, category="AGENT", intent="agent_installation"):
    return {"kb_entry_id": kb_id, "category": category, "intent": intent,
            "instruction": question, "response": answer}


_INSTALL = ("Download the agent installer from the Admin page, copy it to the server, run it with "
            "administrator rights and enter the device key when asked. When the installation finishes "
            "the agent registers itself, and the server appears under Server Monitors within a few "
            "minutes. If it does not, check that outbound port 443 is open.")


def test_variants_of_one_answer_are_one_topic():
    index = kb_retrieval.build_index([
        _entry("KB-1", "how do I install the agent", _INSTALL),
        _entry("KB-2", "How Do I Install The Agent please", "I understand you're looking for help. " + _INSTALL),
        _entry("KB-3", "install agent on Prod-WebServer-01", _INSTALL.replace("the server", "Prod-WebServer-01")),
        _entry("KB-4", "how do I uninstall the agent", "Stop the agent service, then remove the package.",
               intent="agent_uninstall"),
        _entry("KB-5", "my agent is not reporting", "Restart the agent service and check outbound port 443.",
               intent="agent_not_reporting"),
    ])
    assert len(set(index.topics)) == 3
    hits = index.search("how do I install the agent", top_k=3)
    assert [h.entry.intent for h in hits][0] == "agent_installation"
    assert len({h.entry.intent for h in hits}) == len(hits)
    assert hits[0].entry.kb_entry_id == "KB-1"  # the best-matching phrasing


def test_same_answer_in_another_intent_is_not_merged():
    index = kb_retrieval.build_index([
        _entry("KB-1", "install the agent", _INSTALL),
        _entry("KB-2", "install the agent", _INSTALL, category="SERVER_MONITORING", intent="server_setup"),
    ])
    assert len(set(index.topics)) == 2


def test_real_kb_top_results_are_distinct_topics():
    """The real KB: 386 entries, one answer per intent in many phrasings."""
    index = kb_retrieval._load_index()
    assert len(set(index.topics)) < len(index.entries) / 5
    for query in ("why was my device not discovered", "how do I install the Site24x7 agent",
                  "I want you to help on the website monitor", "monitor down alert"):
        hits = kb_retrieval.retrieve_candidates(query, top_k=3, min_score=0)
        topics = [index.topics[index.entries.index(h.entry)] for h in hits]
        assert len(topics) == len(set(topics)), query


# ---------------------------------------------------------------------------
# KB_MIN_SCORE: a weak best match is "no close match"
# ---------------------------------------------------------------------------
def test_weak_match_is_no_match_by_default(monkeypatch):
    monkeypatch.delenv("KB_MIN_SCORE", raising=False)
    query = "I see unable to connect to device error"
    weak = kb_retrieval.retrieve_candidates(query, min_score=0)
    assert weak and weak[0].score < kb_retrieval.DEFAULT_MIN_SCORE == 0.35
    assert kb_retrieval.retrieve_candidates(query) == []


def test_min_score_is_configurable(monkeypatch):
    query = "I see unable to connect to device error"
    monkeypatch.setenv("KB_MIN_SCORE", "0.2")
    assert kb_retrieval.retrieve_candidates(query)
    monkeypatch.setenv("KB_MIN_SCORE", "0.99")
    assert kb_retrieval.retrieve_candidates("how do I install the Site24x7 agent") != []  # exact: 1.0
    # A good match that isn't exact (0.8; "website monitor" alone is now
    # an exact match for the approved KB-N0143).
    assert kb_retrieval.retrieve_candidates("my website monitor alerts too often") == []


@pytest.mark.parametrize("raw, expected", [("", 0.35), ("abc", 0.35), ("0.5", 0.5), ("-1", 0.0), ("7", 1.0)])
def test_min_score_setting_is_parsed_safely(monkeypatch, raw, expected):
    monkeypatch.setenv("KB_MIN_SCORE", raw)
    assert kb_retrieval.kb_min_score() == expected


def test_no_close_match_reaches_the_pipeline_as_no_candidates(monkeypatch):
    monkeypatch.delenv("KB_MIN_SCORE", raising=False)
    state = {"ticket_text": "Customer: I see unable to connect to device error"}
    assert retrieve_kb_candidates(state) == {"kb_candidates": []}


# ---------------------------------------------------------------------------
# Only the customer's own words feed retrieval, every line of them
# ---------------------------------------------------------------------------
def test_customer_text_keeps_multi_line_messages_and_drops_ai_steps():
    transcript = (
        "Customer: my website monitor shows down\n"
        "but the site loads fine in my browser\n"
        "Agent (AI): Try these steps:\n"
        "1. Check the monitor's location settings.\n"
        "Customer: still down\n"
        "staff: Looking into it.\n"
    )
    assert _customer_authored_text(transcript) == (
        "my website monitor shows down\nbut the site loads fine in my browser\nstill down")
