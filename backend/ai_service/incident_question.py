"""incident_question: is a customer asking whether there was (or is) a
known problem — "was there an outage yesterday?", "were you down at 3pm?",
"is this a known issue?" — and for which time window?

Relative dates are resolved by the model against the current time in IST
(given in the data block) and returned as absolute ISO times with an
offset. The output is checked: both ends present and timezone-aware,
start <= end, not in the future, not older than the memory window. The
answer itself never comes from the model — backend/incident_memory.py
builds it from stored, staff-approved summaries.
"""

from __future__ import annotations

from datetime import timedelta

from backend.ai_service import provider
from backend.ai_service.schemas import CATEGORIES, IncidentQuestion, IncidentQuestionInput
from backend.timeutil import parse


def parse_incident_question(data: IncidentQuestionInput) -> IncidentQuestion:
    now = parse(data.now_ist)

    def check(result: IncidentQuestion) -> None:
        if not result.is_incident_question:
            return
        if result.window_start is None or result.window_end is None:
            raise ValueError("an incident question needs a time window")
        if result.window_start.tzinfo is None or result.window_end.tzinfo is None:
            raise ValueError("window times need a UTC offset")
        if result.window_start > result.window_end:
            raise ValueError("window starts after it ends")
        if result.window_end > now + timedelta(hours=1):
            raise ValueError("window is in the future")
        if result.window_start < now - timedelta(days=data.memory_days + 1):
            raise ValueError("window is older than the memory period")

    return provider.complete_json(
        "Decide whether the customer's message asks if there was, or currently is, "
        "a known problem, outage or incident on the Site24x7 side (e.g. 'was there "
        "an outage yesterday?', 'were you down at 3pm?', 'is this a known issue?', "
        "'did something happen last night?'). Requests for help with their own "
        "setup are NOT incident questions. If it is one, return the time window it "
        "asks about as absolute ISO 8601 times WITH the +05:30 offset, resolving "
        "relative words against current_time_ist: a day ('yesterday') is that whole "
        "day 00:00-23:59 IST; a clock time ('at 3pm') is one hour either side; "
        "'last night' is 20:00 to 06:00; present-tense questions ('is this a known "
        "issue?', 'are you down?') cover the last 6 hours up to now. Never return a "
        "window in the future. Also return the product area as category (one of: "
        f"{', '.join(CATEGORIES)}) only if the message names one, else null, and a "
        "short topic phrase if any.",
        {"current_time_ist": now.isoformat(), "customer_message": data.message},
        IncidentQuestion,
        check=check,
    )
