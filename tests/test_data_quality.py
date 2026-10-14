"""Data-quality tests for the silver tables (tickets, resolutions).

Run with: pytest tests/
Requires the pipeline to have been run first (bronze -> silver).
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "pipeline"))
import config  # noqa: E402

CATEGORIES = set(config.CATEGORIES)
INTENTS = set(config.INTENTS)


@pytest.fixture(scope="module")
def tickets() -> pd.DataFrame:
    path = config.SILVER_DIR / "tickets.parquet"
    if not path.exists():
        pytest.skip("silver/tickets.parquet not found; run the pipeline first")
    return pd.read_parquet(path)


@pytest.fixture(scope="module")
def resolutions() -> pd.DataFrame:
    path = config.SILVER_DIR / "resolutions.parquet"
    if not path.exists():
        pytest.skip("silver/resolutions.parquet not found; run the pipeline first")
    return pd.read_parquet(path)


# ---------------------------------------------------------------------------
# Required fields / nulls
# ---------------------------------------------------------------------------

TICKETS_REQUIRED = [
    "ticket_id", "created_at", "channel", "customer_id",
    "category", "intent", "instruction_text", "priority",
]
RESOLUTIONS_REQUIRED = [
    "ticket_id", "response_text", "resolved_by", "resolution_status",
    "handle_time_seconds",
]


@pytest.mark.parametrize("col", TICKETS_REQUIRED)
def test_tickets_required_field_not_null(tickets, col):
    assert tickets[col].notna().all(), f"tickets.{col} has null values"


@pytest.mark.parametrize("col", RESOLUTIONS_REQUIRED)
def test_resolutions_required_field_not_null(resolutions, col):
    assert resolutions[col].notna().all(), f"resolutions.{col} has null values"


def test_tickets_required_fields_not_empty_string(tickets):
    for col in ("instruction_text",):
        assert (tickets[col].str.len() > 0).all()


# ---------------------------------------------------------------------------
# Uniqueness / referential integrity
# ---------------------------------------------------------------------------

def test_ticket_id_unique(tickets):
    assert tickets["ticket_id"].is_unique


def test_resolutions_ticket_id_unique(resolutions):
    assert resolutions["ticket_id"].is_unique


def test_every_resolution_has_a_ticket(tickets, resolutions):
    orphaned = set(resolutions["ticket_id"]) - set(tickets["ticket_id"])
    assert not orphaned, f"resolutions reference unknown ticket_ids: {orphaned}"


def test_every_ticket_has_a_resolution(tickets, resolutions):
    missing = set(tickets["ticket_id"]) - set(resolutions["ticket_id"])
    assert not missing, f"tickets missing a resolution record: {missing}"


def test_kb_entry_id_not_null(tickets):
    assert tickets["kb_entry_id"].notna().all()


def test_kb_entry_id_format(tickets):
    assert tickets["kb_entry_id"].str.match(r"^KB-\d{4}$").all()


def test_kb_entry_id_count_is_far_smaller_than_ticket_count(tickets):
    # This dataset has 386 unique source instructions duplicated ~5x each
    # under different `flags` values, not near-unique rows like the old
    # Bitext source -- kb_entry_id collapses those duplicates down to the
    # canonical KB entry each ticket was drawn from.
    n_entries = tickets["kb_entry_id"].nunique()
    assert 0 < n_entries < len(tickets) * 0.1


# ---------------------------------------------------------------------------
# Enum / domain checks
# ---------------------------------------------------------------------------

def test_category_values_are_known(tickets):
    unknown = set(tickets["category"].unique()) - CATEGORIES
    assert not unknown, f"unexpected category values: {unknown}"


def test_intent_values_are_known(tickets):
    unknown = set(tickets["intent"].unique()) - INTENTS
    assert not unknown, f"unexpected intent values: {unknown}"


def test_intent_belongs_to_its_category(tickets):
    bad_rows = tickets[~tickets.apply(
        lambda row: row["intent"] in config.CATEGORY_INTENTS.get(row["category"], []),
        axis=1,
    )]
    assert bad_rows.empty, f"{len(bad_rows)} rows have intent/category mismatches"


def test_channel_values_are_known(tickets):
    assert set(tickets["channel"].unique()) <= set(config.CHANNELS)


def test_priority_values_are_known(tickets):
    assert set(tickets["priority"].unique()) <= set(config.PRIORITY_VALUES)


def test_resolved_by_values_are_known(resolutions):
    assert set(resolutions["resolved_by"].unique()) <= set(config.RESOLVED_BY_VALUES)


def test_resolution_status_values_are_known(resolutions):
    assert set(resolutions["resolution_status"].unique()) <= set(config.RESOLUTION_STATUS_VALUES)


# ---------------------------------------------------------------------------
# Range / correlation checks
# ---------------------------------------------------------------------------

def test_csat_score_in_range_or_null(resolutions):
    scores = resolutions["csat_score"].dropna()
    assert scores.between(1, 5).all()


def test_handle_time_positive(resolutions):
    assert (resolutions["handle_time_seconds"] > 0).all()


def test_pending_tickets_have_no_csat(resolutions):
    pending = resolutions[resolutions["resolution_status"] == "pending"]
    assert pending["csat_score"].isna().all()


def test_created_at_within_trailing_12_months(tickets):
    now = pd.Timestamp.now()
    one_year_ago = now - pd.Timedelta(days=370)  # small buffer
    assert (tickets["created_at"] >= one_year_ago).all()
    assert (tickets["created_at"] <= now + pd.Timedelta(days=1)).all()


def test_language_tags_are_decoded_not_raw_letters(tickets):
    # every decoded tag should be a known legend value, not a bare letter
    all_tags = {tag for tags in tickets["language_tags"] for tag in tags}
    assert all_tags <= set(config.FLAG_LEGEND.values())
