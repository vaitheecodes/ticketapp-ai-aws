"""Detects a short closing/acknowledgment message on an already-resolved
ticket ("thanks", "got it", "that worked") so a follow-up chat message
doesn't blindly re-run the full classify/decide_route pipeline and
escalate a solved ticket just because "thanks" isn't a support request
and gets classified with near-zero confidence.

Deliberately conservative: this is a shortcut for the *unambiguous*
case only. Anything with a negation/continuation signal ("but", "still",
"not"), a question mark, or more than a handful of words falls through
to the normal pipeline instead — a customer saying "thanks, but it's
still not working" or "thanks, one more question" must still get
reclassified, not silently swallowed as a close.
"""

from __future__ import annotations

import re

from backend.text_normalize import normalize_apostrophes

# Any of these anywhere in the message means it's NOT a bare
# acknowledgment, regardless of how it starts — err toward the normal
# pipeline whenever there's a hint of continued or new substance.
_CONTINUATION_SIGNALS = re.compile(
    r"\b(but|however|still|not|n't|another|also|one more|additionally|"
    r"besides|except|although|though|actually|wait|"
    r"what about|can you also|one thing|question|help with|"
    r"also need|still need)\b",
    re.IGNORECASE,
)

# Normalized (lowercase, punctuation stripped, whitespace collapsed)
# closing phrases. Exact-match only — deliberately not fuzzy, so this
# stays predictable and easy to extend.
_ACK_PHRASES = frozenset(
    {
        "thanks",
        "thank you",
        "thanks a lot",
        "thanks so much",
        "thank you so much",
        "thank you very much",
        "many thanks",
        "much appreciated",
        "appreciate it",
        "appreciated",
        "really appreciate it",
        "got it",
        "got it thanks",
        "got it thank you",
        "ok",
        "okay",
        "ok thanks",
        "okay thanks",
        "perfect",
        "perfect thanks",
        "great",
        "great thanks",
        "awesome",
        "awesome thanks",
        "cool",
        "cool thanks",
        "nice",
        "sounds good",
        "sounds great",
        "all set",
        "all good",
        "all good now",
        "that worked",
        "that worked thanks",
        "this worked",
        "that solved it",
        "this solved it",
        "that fixed it",
        "this fixed it",
        "issue resolved",
        "problem solved",
        "resolved thanks",
        "no further questions",
        "nothing else",
        "nothing else for now",
        "thats all",
        "that is all",
        "you can close this",
        "you can close this ticket",
        "you can close the ticket",
        "please close this ticket",
        "close this ticket",
    }
)

_MAX_ACK_WORDS = max(len(p.split()) for p in _ACK_PHRASES)


def _normalize(text: str) -> str:
    # Typographic apostrophes first — "that’s all" must normalize the
    # same as "that's all", or the curly variant silently misses.
    cleaned = re.sub(r"[^a-z0-9\s']", " ", normalize_apostrophes(text).strip().lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def is_closing_acknowledgment(text: str) -> bool:
    """True only for an unambiguous, content-free closing message. Any
    hint of new/continued substance (negation, a question, more than a
    handful of words, or wording outside the known closing phrases)
    returns False so the caller falls back to the normal
    classify/decide_route pipeline instead of silently closing a ticket
    that still needs attention."""
    stripped = text.strip()
    if not stripped or "?" in stripped:
        return False
    if _CONTINUATION_SIGNALS.search(stripped):
        return False

    normalized = _normalize(stripped)
    if not normalized or len(normalized.split()) > _MAX_ACK_WORDS:
        return False

    return normalized in _ACK_PHRASES
