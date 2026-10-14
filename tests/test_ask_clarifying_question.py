"""Tests that ask_clarifying_question's LLM prompt is actually grounded
in the retrieved KB candidates (bug report fix bullet: "the prompt must
name the SPECIFIC candidates it's choosing between... rather than
generating a free-floating meta-question about categories/subscriptions
that don't exist in this taxonomy").

Stubs the Azure OpenAI client so this is a fast, deterministic,
network-free check of prompt construction — it does not assert on what
a real LLM would ask, only on what evidence it was given to ask from.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import graph  # noqa: E402


class _StubCompletions:
    def __init__(self, captured: list[dict]):
        self._captured = captured

    def create(self, *, model, messages, **kwargs):
        self._captured.append({"model": model, "messages": messages})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="stub question"))]
        )


class _StubClient:
    def __init__(self, captured: list[dict]):
        self.chat = SimpleNamespace(completions=_StubCompletions(captured))


def _invoke_ask_clarifying_question(monkeypatch, kb_candidates):
    captured: list[dict] = []
    monkeypatch.setattr(graph, "_client", _StubClient(captured))
    graph.ask_clarifying_question(
        {
            "ticket_text": "some ambiguous ticket text",
            "category": "SUPPORT",
            "confidence": 0.5,
            "kb_candidates": kb_candidates,
        }
    )
    assert captured, "expected the stub client to be called"
    system_prompt = captured[0]["messages"][0]["content"]
    return system_prompt


def test_grounded_prompt_names_specific_candidates(monkeypatch):
    candidates = [
        {
            "kb_entry_id": "KB-0001",
            "category": "ALERTS",
            "intent": "alert_configuration",
            "instruction": "how do I configure an alert profile in Site24x7",
            "response": "...",
            "score": 0.455,
        },
        {
            "kb_entry_id": "KB-0002",
            "category": "ALERTS",
            "intent": "alert_not_triggered",
            "instruction": "Site24x7 alert notification was not triggered",
            "response": "...",
            "score": 0.375,
        },
    ]
    system_prompt = _invoke_ask_clarifying_question(monkeypatch, candidates)

    # Must reference the actual retrieved intents/example questions...
    assert "alert_configuration" in system_prompt
    assert "alert_not_triggered" in system_prompt
    assert "how do I configure an alert profile in Site24x7" in system_prompt
    # ...and must not fall back to the old free-floating "which category"
    # framing that lists the stale Bitext-era categories as live options.
    assert "SUBSCRIPTION" not in system_prompt
    assert "REFUND" not in system_prompt
    assert "CANCEL" not in system_prompt


def test_no_candidates_falls_back_to_generic_grounded_prompt(monkeypatch):
    system_prompt = _invoke_ask_clarifying_question(monkeypatch, [])
    assert "No close match was found" in system_prompt
    assert "SUBSCRIPTION" not in system_prompt
    assert "REFUND" not in system_prompt
