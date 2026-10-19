"""Times are stored in UTC (ISO 8601) and shown to people in IST.

IST is a fixed UTC+05:30 (no daylight saving), so a fixed offset is exact.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse(value: str | datetime) -> datetime:
    """ISO string or datetime -> aware datetime. A naive value is treated
    as IST (that's what staff type into the resolve form)."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt


def to_utc_iso(value: str | datetime) -> str:
    return parse(value).astimezone(timezone.utc).isoformat()


def format_ist(value: str | datetime | None) -> str:
    """'19 Oct 2026, 15:30 IST' — the one format customers and staff see."""
    if not value:
        return "unknown time"
    return parse(value).astimezone(IST).strftime("%-d %b %Y, %H:%M IST")
