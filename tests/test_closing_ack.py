"""Pure-function tests for backend/closing_ack.py's bare-acknowledgment
detector — see tests/test_ticket_chat_closing.py for the endpoint-level
integration tests of the bug this exists to fix."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.closing_ack import is_closing_acknowledgment  # noqa: E402


@pytest.mark.parametrize(
    "text",
    [
        "thanks",
        "thank you",
        "got it",
        "ok",
        "okay",
        "perfect",
        "great",
        "that worked",
        "Thanks!",
        "Thank you so much!",
        "Got it, thanks.",
        "  thanks  ",
        "That solved it.",
        "All good now!",
        "you can close this ticket",
    ],
)
def test_bare_acknowledgments_detected(text):
    assert is_closing_acknowledgment(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "thanks, but it is still not working",
        "thanks, but it's still not working",
        "thanks, one more question",
        "thank you, can you also help with billing",
        "thanks but the monitor is still showing down",
        "ok, one more thing",
        "not resolved",
        "this is still broken",
        "great, but what about the API monitor?",
        "I have another issue",
        "what about Windows?",
        "",
        "   ",
        "can you also check my other monitor",
        "thanks for nothing, this is useless",
    ],
)
def test_non_acknowledgments_fall_through(text):
    assert is_closing_acknowledgment(text) is False


def test_questions_never_treated_as_acknowledgment_even_if_short():
    assert is_closing_acknowledgment("ok?") is False


def test_long_message_never_treated_as_acknowledgment():
    long_thanks = "thanks " * 10
    assert is_closing_acknowledgment(long_thanks) is False
