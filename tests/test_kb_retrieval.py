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
        _customer_authored_text(transcript), top_k=1
    )
    assert candidates
    assert candidates[0].entry.category == "WEB_MONITORING"


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
