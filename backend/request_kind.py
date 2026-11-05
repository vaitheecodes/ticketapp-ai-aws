"""What kind of request a message (or a KB entry's question) is.

- how-to: the customer wants to add, set up or configure something
  ("I need to add an APM monitor, how to do?"). Never a fault: nothing is
  broken, so there is no error to ask about and no troubleshooting entry
  can answer it.
- fault: something is broken or missing ("monitor shows down", "unable
  to log in", "alerts not received").
- error_reported: the customer mentioned an error (text, code, failure).
  Only then may a reply ask for the exact error message.

Plain regular expressions: cheap, deterministic, and easy to test. Used by
graph.py (routing, grounding) and kb_retrieval.py (how-to searches skip
troubleshooting entries).
"""

from __future__ import annotations

import re

_FAULT = re.compile(
    r"\b(errors?|fail\w*|down|outage|broken|stopped|missing|wrong|false|issues?|problems?"
    r"|unable|cannot|can'?t|couldn'?t|won'?t|isn'?t|aren'?t|doesn'?t|don'?t|didn'?t|not"
    r"|suspended|crash\w*|timeouts?|timed out|denied|invalid|rejected|troubleshoot\w*"
    r"|no (data|alerts?|metrics|emails?))\b",
    re.IGNORECASE)
_ERROR = re.compile(
    r"\b(errors?|fail\w*|exceptions?|denied|invalid|rejected|timed out|timeouts?|error code"
    r"|status code|http \d{3}|\d{3} (error|response))\b",
    re.IGNORECASE)
_TASK = re.compile(
    r"\b(add|adding|set ?up|set (?:it|this|that|them) up|setting up|configure|configuring|create|creating|install|installing"
    r"|enable|enabling|integrate|integrating|connect|connecting|monitor|monitoring|track|tracking"
    r"|change|changing|update|updating|edit|modify|generate|schedule)\b",
    re.IGNORECASE)
# Task verbs as single words (graph.shares_customer_words compares the
# objects of a request, not its verb).
TASK_WORDS = frozenset("""
    add adding set setup setting configure configuring create creating install installing enable
    enabling integrate integrating connect connecting monitor monitoring track tracking change
    changing update updating edit modify generate schedule
""".split())
_HOW = re.compile(
    r"\b(how|steps?|guide|way to|want to|wants to|need to|needs to|would like to|like to|trying to"
    r"|wanna|planning to|is it possible to|can i|can we|could i)\b",
    re.IGNORECASE)
_UNSURE = re.compile(r"\b(not sure|unsure|no idea|don'?t know|dunno|idk|not certain)\b", re.IGNORECASE)
_STARTS_WITH_TASK = re.compile(r"^\s*(please\s+)?(add|set ?up|configure|create|install|enable|integrate|connect)\b",
                               re.IGNORECASE)


def is_fault(text: str) -> bool:
    return bool(_FAULT.search(text or ""))


def error_reported(text: str) -> bool:
    return bool(_ERROR.search(text or ""))


def is_unsure(text: str) -> bool:
    """"Not sure", "no idea": an answer that gives nothing, not a fault."""
    return bool(_UNSURE.search(text or ""))


def mentions_task(text: str) -> bool:
    """Names something to add, set up, configure or change (a KB entry
    that can answer a how-to does)."""
    return bool(_TASK.search(text or ""))


def is_how_to(text: str) -> bool:
    """A request to add, set up or configure something, with no sign of a
    fault."""
    text = text or ""
    if is_fault(text) or not _TASK.search(text):
        return False
    return bool(_HOW.search(text) or _STARTS_WITH_TASK.search(text))
