"""Tests for the continuation-vs-new-topic pieces in backend/graph.py
that back the "what is the difference" bug fix (see
tests/test_ticket_chat_closing.py for the endpoint-level integration
tests of the full fix).

is_continuation_of_prior_response makes a real LLM call, so it's tested
here with a stubbed Azure OpenAI client (prompt-construction /
response-parsing only, no network) — same pattern as
tests/test_ask_clarifying_question.py. has_escalation_language and
retrieve_kb_candidates_for_continuation are pure/DB-free and tested
directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import graph, kb_retrieval  # noqa: E402
from backend.ai_service import provider  # noqa: E402


def _invoke_is_continuation(monkeypatch, response_json, message_text, prior_ai_response):
    captured: list[dict] = []

    def _fake_complete(messages, **kwargs):
        captured.append({"messages": messages, "kwargs": kwargs})
        return response_json

    monkeypatch.setattr(provider, "complete", _fake_complete)
    result = graph.is_continuation_of_prior_response(message_text, prior_ai_response)
    assert captured, "expected the provider to be called"
    return result, captured[0]


def test_continuation_true_is_parsed(monkeypatch):
    result, call = _invoke_is_continuation(
        monkeypatch,
        json.dumps({"is_continuation": True}),
        "what is the differnce",
        "A Website Monitor checks page load; a Transaction Monitor checks a user flow.",
    )
    assert result is True
    # grounding check: both the new message and the prior reply must
    # actually be in the prompt sent to the model
    user_content = call["messages"][1]["content"]
    assert "what is the differnce" in user_content
    assert "Transaction Monitor" in user_content


def test_continuation_false_is_parsed(monkeypatch):
    result, _ = _invoke_is_continuation(
        monkeypatch,
        json.dumps({"is_continuation": False}),
        "separately, how do I install the agent on Ubuntu",
        "A Website Monitor checks page load; a Transaction Monitor checks a user flow.",
    )
    assert result is False


def test_missing_key_defaults_to_false(monkeypatch):
    result, _ = _invoke_is_continuation(monkeypatch, json.dumps({}), "hmm", "prior reply")
    assert result is False


def test_uses_json_mode_and_zero_temperature(monkeypatch):
    _, call = _invoke_is_continuation(
        monkeypatch, json.dumps({"is_continuation": True}), "msg", "prior"
    )
    assert call["kwargs"]["json_mode"] is True
    assert call["kwargs"]["temperature"] == 0


# ---------------------------------------------------------------------------
# has_escalation_language
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "thanks, but it's still not working",
        "this is useless and ridiculous",
        "not resolved, and also still broken, so I guess that's it then",
    ],
)
def test_escalation_language_detected(text):
    assert graph.has_escalation_language(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "what is the differnce",
        "how do I install the agent on Ubuntu",
        "thanks, that worked",
    ],
)
def test_no_escalation_language(text):
    assert graph.has_escalation_language(text) is False


# ---------------------------------------------------------------------------
# retrieve_kb_candidates_for_continuation
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _require_silver_tables():
    if kb_retrieval._load_index() is None:
        pytest.skip("data/silver/*.parquet not found; run the pipeline first")


def test_continuation_retrieval_uses_the_customers_earlier_messages():
    """A short fragment alone ("what is the differnce") has almost no
    topic vocabulary; the customer's earlier messages carry the topic."""
    transcript = (
        "Customer: I want to set up a website monitor\n"
        "Agent (AI): A Website Monitor checks that your site loads.\n"
        "Customer: what is the differnce"
    )
    candidates = graph.retrieve_kb_candidates_for_continuation(transcript)
    assert candidates
    assert candidates[0]["category"] == "WEB_MONITORING"


def test_continuation_retrieval_never_searches_the_ai_reply():
    """The AI's previous reply talks about installing the agent; the
    customer only ever talked about website monitors. Retrieval follows
    the customer, and the AI's wording can't raise any score."""
    ai_reply = ("To install the Site24x7 agent, download the agent installer, run it "
                "on the server and the agent starts reporting.")
    customer_only = "Customer: I want to set up a website monitor\nCustomer: can you say more"
    with_ai = ("Customer: I want to set up a website monitor\n"
               f"Agent (AI): {ai_reply}\n1. Download the agent installer.\n2. Install the Site24x7 agent.\n"
               "Customer: can you say more")
    assert graph.retrieve_kb_candidates_for_continuation(with_ai) == \
        graph.retrieve_kb_candidates_for_continuation(customer_only)
    assert all(c["intent"] != "agent_installation"
               for c in graph.retrieve_kb_candidates_for_continuation(with_ai))


def test_continuation_retrieval_with_only_a_vague_fragment_finds_no_match():
    transcript = ("Customer: hello\nAgent (AI): How do I install the Site24x7 agent? Download it.\n"
                  "Customer: can you say more about that")
    assert graph.retrieve_kb_candidates_for_continuation(transcript) == []


def test_continuation_retrieval_returns_dicts_shaped_like_normal_retrieval():
    candidates = graph.retrieve_kb_candidates_for_continuation(
        "Customer: how do I install the Site24x7 agent\nAgent (AI): Download it.\n"
        "Customer: can you say more about that")
    assert candidates
    for key in ("kb_entry_id", "category", "intent", "instruction", "response", "score"):
        assert key in candidates[0]
