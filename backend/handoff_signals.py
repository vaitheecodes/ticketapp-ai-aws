"""Detects two hand-off situations the confidence-based routing misses,
because in both cases the AI classified and answered with perfectly
high confidence — the problem is *what it answered*, not how sure it was:

  1. `admits_limitation` — the AI's own reply concedes it can't actually
     do the thing ("I don't have direct access to billing or account
     details", "please contact our Sales team"). Nothing was resolved, so
     leaving the ticket `resolved` strands the customer with self-service
     text instead of putting a human on it.
  2. `is_escalation_acceptance` — the AI offered to connect the customer
     with a human and the customer said yes ("yes please"). That's an
     acceptance of a hand-off, not a fresh support request to classify;
     re-running the pipeline on it just produced *more* DIY navigation
     text instead of a queued ticket.

Both are keyword/regex checks over text this system itself produced (the
AI's own reply) plus a short affirmative, so they're deterministic and
need no extra LLM call — same approach as backend/closing_ack.py. The
limitation patterns in particular target phrasing generate_response is
explicitly *instructed* to use by its own grounding rule ("say plainly
that you don't have access to it"), which is what makes a regex
reliable here rather than brittle.
"""

from __future__ import annotations

import re

from backend.text_normalize import normalize_apostrophes

# First-person admissions that the AI can't see/do the thing, plus
# referrals to Site24x7's own human teams. Deliberately scoped: a
# generic "you'll need to contact your network team to open port 443" is
# a legitimate resolution, not a hand-off signal, so referrals only
# count when they point at *our* sales/billing/support/accounts team.
_I_CANNOT = r"(?:i can't|i cannot|i am unable to|i'm unable to|i am not able to|i'm not able to)"
_I_LACK = r"(?:i don't|i do not|i can't|i cannot)"

_LIMITATION_PATTERNS = re.compile(
    rf"{_I_LACK} have (?:direct |immediate )?access to"
    rf"|{_I_LACK} have (?:the )?(?:ability|visibility|permission)"
    rf"|{_I_LACK} have visibility into"
    rf"|{_I_CANNOT} (?:directly )?"
    r"(?:view|see|check|access|retrieve|look up|verify|confirm|"
    r"pull up|modify|change|update|cancel|issue|process)"
    r"|(?:contact|reach out to|get in touch with) "
    r"(?:our|the|site24x7'?s?) (?:sales|billing|accounts?|support|customer success)"
    r"|only (?:our|the) (?:sales|billing|accounts?|support) team can"
    r"|(?:will|would) need (?:a human|an agent|our team|our support team|"
    r"someone from our)"
    r"|i'?ll need to (?:escalate|hand this off|hand you off)",
    re.IGNORECASE,
)

# The AI offering to put the customer in touch with a human. Gates the
# affirmative check below: "ok" only means "yes, escalate me" when
# something was actually offered.
_HANDOFF_OFFER_PATTERNS = re.compile(
    r"connect you (?:with|to)"
    r"|put you in touch"
    r"|guide you on how to (?:reach|contact)"
    r"|help you (?:reach|contact|get in touch)"
    r"|transfer you"
    r"|escalate (?:this|it) (?:to|for)"
    r"|(?:would you like|shall i|do you want|should i) "
    r"(?:me to )?(?:help|guide|connect|escalate|raise|create|open)"
    r"|(?:raise|create|open) a (?:support )?ticket",
    re.IGNORECASE,
)

# Clear agreement. Deliberately excludes bare gratitude ("thanks") so a
# "thanks" after a hand-off offer still reads as a close, not an
# escalation — see backend/closing_ack.py.
_AFFIRMATIVE_PHRASES = frozenset(
    {
        "yes",
        "yes please",
        "yes pls",
        "yes thanks",
        "yes thank you",
        "yes do that",
        "yes please do",
        "yes go ahead",
        "yes connect me",
        "yes escalate",
        "yes raise a ticket",
        "please",
        "please do",
        "please do that",
        "please do so",
        "please connect me",
        "please escalate",
        "please raise a ticket",
        "connect me",
        "connect me please",
        "escalate please",
        "escalate it",
        "escalate this",
        "do that",
        "do it",
        "go ahead",
        "go ahead please",
        "sure",
        "sure please",
        "ok",
        "okay",
        "ok please",
        "okay please",
        "ok do that",
        "okay do that",
        "that would be great",
        "that would be helpful",
        "that would help",
        "i would like that",
        "id like that",
        "sounds good please",
        "raise a ticket",
        "raise a ticket please",
        "open a ticket",
    }
)

_MAX_AFFIRMATIVE_WORDS = max(len(p.split()) for p in _AFFIRMATIVE_PHRASES)


def _normalize(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s']", " ", normalize_apostrophes(text).strip().lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def admits_limitation(ai_response: str) -> bool:
    """True if the AI's own reply concedes it can't do/see the thing, or
    refers the customer to one of Site24x7's human teams. Such a reply
    hasn't resolved the ticket, however confidently it was classified."""
    return bool(ai_response) and bool(
        _LIMITATION_PATTERNS.search(normalize_apostrophes(ai_response))
    )


def offers_human_handoff(ai_response: str) -> bool:
    """True if the AI offered to connect the customer with a human."""
    return bool(ai_response) and bool(
        _HANDOFF_OFFER_PATTERNS.search(normalize_apostrophes(ai_response))
    )


def is_escalation_acceptance(message_text: str, prior_ai_response: str) -> bool:
    """True when the customer is accepting a hand-off the AI just
    offered — a short, unambiguous affirmative in reply to an offer to
    connect/escalate. Both halves are required: an affirmative with no
    offer on the table is not an escalation request, and an offer
    followed by a question or new detail is a normal follow-up."""
    if not prior_ai_response or not offers_human_handoff(prior_ai_response):
        return False
    if "?" in message_text:
        return False

    normalized = _normalize(message_text)
    if not normalized or len(normalized.split()) > _MAX_AFFIRMATIVE_WORDS:
        return False
    return normalized in _AFFIRMATIVE_PHRASES
