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
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import graph, kb_retrieval  # noqa: E402


class _StubCompletions:
    def __init__(self, response_json: str, captured: list[dict]):
        self._response_json = response_json
        self._captured = captured

    def create(self, *, model, messages, **kwargs):
        self._captured.append({"model": model, "messages": messages, "kwargs": kwargs})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._response_json))]
        )


class _StubClient:
    def __init__(self, response_json: str, captured: list[dict]):
        self.chat = SimpleNamespace(completions=_StubCompletions(response_json, captured))


def _invoke_is_continuation(monkeypatch, response_json, message_text, prior_ai_response):
    captured: list[dict] = []
    monkeypatch.setattr(graph, "_client", _StubClient(response_json, captured))
    result = graph.is_continuation_of_prior_response(message_text, prior_ai_response)
    assert captured, "expected the stub client to be called"
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


def test_uses_json_response_format_and_zero_temperature(monkeypatch):
    _, call = _invoke_is_continuation(
        monkeypatch, json.dumps({"is_continuation": True}), "msg", "prior"
    )
    assert call["kwargs"]["response_format"] == {"type": "json_object"}
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


def test_continuation_retrieval_uses_prior_reply_for_recall():
    """A short fragment alone ("what is the differnce") has almost no
    topic vocabulary; the prior AI reply carries the actual substance,
    so retrieval must use both -- this is the same signal-to-noise fix
    as retrieve_kb_candidates' stopword filtering, applied to a
    different noise source (an uninformative short query)."""
    prior_reply = (
        "A Website Monitor checks that your website loads correctly, while an "
        "API Monitor validates specific API responses in Site24x7."
    )
    candidates = graph.retrieve_kb_candidates_for_continuation(
        prior_reply, "what is the differnce"
    )
    assert candidates
    assert candidates[0]["category"] == "WEB_MONITORING"


def test_continuation_retrieval_returns_dicts_shaped_like_normal_retrieval():
    candidates = graph.retrieve_kb_candidates_for_continuation(
        "how do I install the Site24x7 agent", "can you say more about that"
    )
    assert candidates
    for key in ("kb_entry_id", "category", "intent", "instruction", "response", "score"):
        assert key in candidates[0]
