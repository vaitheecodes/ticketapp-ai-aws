"""Pure-function tests for backend/handoff_signals.py — the two
hand-off signals that confidence-based routing misses (the AI answered
confidently, but its answer concedes it can't help, or the customer is
accepting a hand-off it offered).

See tests/test_handoff_escalation.py for the endpoint-level tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.handoff_signals import (  # noqa: E402
    admits_limitation,
    is_escalation_acceptance,
    offers_human_handoff,
)

_HANDOFF_OFFER = "I can guide you on how to reach our billing team, or connect you with support."


@pytest.mark.parametrize(
    "ai_response",
    [
        # the exact shape from the bug report
        "I don't have direct access to billing or account details. For invoice "
        "or plan specifics, please contact our Sales or Billing team.",
        "I'm unable to view your invoice directly.",
        "I cannot check your account plan from here.",
        "I don't have visibility into your license usage records.",
        "I do not have access to your subscription information.",
        "Only our billing team can confirm the plan change.",
        "This would need a human from our support team to verify.",
        "I don't have the ability to modify your account.",
        "You can reach out to our accounts team for the invoice copy.",
    ],
)
def test_limitation_admissions_detected(ai_response):
    assert admits_limitation(ai_response) is True


@pytest.mark.parametrize(
    "ai_response",
    [
        # real resolved answers from live runs — must not escalate
        "To install the Site24x7 agent, download the installer that matches "
        "your operating system and run it with administrator rights. If you "
        "hit issues, collect the installer output and agent logs.",
        "For website monitoring, configure the URL, monitoring locations, "
        "request or validation settings, and alert profile.",
        "A Transaction Monitor replays recorded steps; a Scripted Browser "
        "Monitor uses Selenium WebDriver for more complex flows.",
        # a referral to someone *else's* team is a legitimate resolution,
        # not a hand-off signal
        "You will need to contact your network team to open port 443 so the "
        "agent can reach our servers.",
        "Please check the monitor's threshold configuration and alert profile.",
        "",
    ],
)
def test_normal_resolutions_not_flagged(ai_response):
    assert admits_limitation(ai_response) is False


def test_typographic_apostrophe_still_matches():
    """Regression guard: LLM output uses curly apostrophes ("I don't
    have access"), and the live invoice scenario escaped this check
    entirely because the pattern only had the ASCII variant."""
    curly = "I don’t have access to your account details or invoices directly."
    assert admits_limitation(curly) is True
    assert admits_limitation(curly.replace("’", "'")) is True


@pytest.mark.parametrize(
    "ai_response",
    [
        _HANDOFF_OFFER,
        "Would you like me to escalate this to a human agent?",
        "Shall I raise a ticket for you?",
        "I can put you in touch with our support team.",
        "Do you want me to connect you with billing?",
    ],
)
def test_handoff_offers_detected(ai_response):
    assert offers_human_handoff(ai_response) is True


def test_normal_answer_is_not_a_handoff_offer():
    assert offers_human_handoff("Here's how to install the agent on Ubuntu.") is False


@pytest.mark.parametrize(
    "message",
    [
        "yes please",
        "yes",
        "Yes, please do.",
        "please do",
        "connect me please",
        "go ahead",
        "sure",
        "ok",
        "escalate please",
        "that would be great",
    ],
)
def test_affirmative_after_offer_is_acceptance(message):
    assert is_escalation_acceptance(message, _HANDOFF_OFFER) is True


def test_affirmative_without_an_offer_is_not_acceptance():
    assert is_escalation_acceptance("yes please", "Here's how to install the agent.") is False


def test_thanks_after_offer_is_not_acceptance():
    """Bare gratitude must stay with the closing-acknowledgment path
    (backend/closing_ack.py), which closes the ticket — only clear
    agreement counts as accepting a hand-off."""
    assert is_escalation_acceptance("thanks", _HANDOFF_OFFER) is False
    assert is_escalation_acceptance("thank you", _HANDOFF_OFFER) is False


@pytest.mark.parametrize(
    "message",
    [
        "yes but it still fails after that step",
        "what about Windows?",
        "yes, and also how do I add a second monitor",
        "",
    ],
)
def test_substantive_or_questioning_replies_are_not_acceptance(message):
    assert is_escalation_acceptance(message, _HANDOFF_OFFER) is False
