"""SLA: a first-response and a resolution target per priority.

Off until the admin turns it on (app_settings "sla"). While it is on, a
ticket gets its targets and due times from its priority when it is
created (ticket_lifecycle.record_created); a priority change recalculates
them from the creation time (on_priority_changed). Tickets created while
SLA was off have no SLA and are left out of SLA reporting.

Clocks
- First response: from creation to the first public reply by a person on
  the support team, or by the AI on a ticket the AI is handling
  (note_first_response, from conversation.insert_ticket_message). An
  incident broadcast (a staff message with no author) doesn't count.
  Never paused.
- Resolution: from creation to Resolved/Closed. It pauses while the ticket
  is Pending Customer (waiting on the customer) and while it is
  Resolved/Closed; a reopen resumes it, so a reopened ticket keeps the time
  it already used (on_status_changed: sla_paused_at,
  sla_paused_seconds).

Calendar: 24x7 for now. All clock arithmetic goes through
add_working_time / working_seconds, so business hours can be added there
later without touching the rest.

State of each target at a moment (evaluate):
  met        done within the target
  breached   done late, or not done and past due
  at_risk    not done, at least `at_risk_percent` of the target used
  on_track   not done, less than that used
  paused     resolution only: the clock is stopped (Pending Customer)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

PRIORITIES = ("low", "medium", "high", "urgent")
# The resolution clock is stopped in these statuses.
PAUSED_STATUSES = frozenset({"pending_customer", "resolved", "closed"})
DONE_STATUSES = frozenset({"resolved", "closed"})


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
def parse(value) -> datetime | None:
    """A stored timestamp (ISO text on SQLite and from the Postgres
    loader) as an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T", 1))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def add_working_time(start: datetime, minutes: int, calendar: str = "24x7") -> datetime:
    """`start` plus `minutes` of working time on `calendar`."""
    if calendar != "24x7":
        raise ValueError(f"Unknown SLA calendar: {calendar}")
    return start + timedelta(minutes=minutes)


def working_seconds(start: datetime, end: datetime, calendar: str = "24x7") -> float:
    """Working seconds between two moments on `calendar` (0 if end <= start)."""
    if calendar != "24x7":
        raise ValueError(f"Unknown SLA calendar: {calendar}")
    return max(0.0, (end - start).total_seconds())


def _settings():
    from backend import app_settings  # app_settings imports conversation, which imports this module

    return app_settings.get("sla")


# ---------------------------------------------------------------------------
# Writes (called inside the caller's transaction)
# ---------------------------------------------------------------------------
def _apply_targets(conn, ticket_id: str, created_at: datetime, priority: str, settings) -> None:
    target = getattr(settings, priority if priority in PRIORITIES else "medium")
    conn.execute(
        "UPDATE tickets SET sla_first_response_minutes = ?, sla_resolution_minutes = ?, "
        "sla_first_response_due = ?, sla_resolution_due = ? WHERE ticket_id = ?",
        (target.first_response_minutes, target.resolution_minutes,
         iso(add_working_time(created_at, target.first_response_minutes, settings.calendar)),
         iso(add_working_time(created_at, target.resolution_minutes, settings.calendar)),
         ticket_id),
    )


def on_created(conn, ticket_id: str) -> None:
    """resolved_at for a ticket created already resolved (an AI answer),
    then, if SLA is on, the targets and due times from the priority."""
    row = conn.execute("SELECT created_at, priority, status FROM tickets WHERE ticket_id = ?",
                       (ticket_id,)).fetchone()
    if row is None:
        return
    created = parse(row["created_at"])
    if row["status"] in DONE_STATUSES:
        conn.execute("UPDATE tickets SET resolved_at = ? WHERE ticket_id = ?", (iso(created), ticket_id))
    settings = _settings()
    if not settings.enabled:
        return
    _apply_targets(conn, ticket_id, created, row["priority"], settings)
    if row["status"] in PAUSED_STATUSES:  # created waiting on the customer, or already resolved
        conn.execute("UPDATE tickets SET sla_paused_at = ? WHERE ticket_id = ?", (iso(created), ticket_id))


def on_priority_changed(conn, ticket_id: str) -> None:
    """New targets for the new priority, counted from creation. A ticket
    that never had an SLA (created while it was off) gets one only if SLA
    is on now."""
    settings = _settings()
    row = conn.execute("SELECT created_at, priority, sla_resolution_minutes FROM tickets "
                       "WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if row is None or (row["sla_resolution_minutes"] is None and not settings.enabled):
        return
    _apply_targets(conn, ticket_id, parse(row["created_at"]), row["priority"], settings)


def on_status_changed(conn, ticket_id: str, old: str | None, new: str, at: str) -> None:
    """Pause/resume the resolution clock and keep resolved_at (the latest
    time the ticket became Resolved or Closed; cleared on reopen)."""
    moment = parse(at)
    row = conn.execute("SELECT sla_paused_at, sla_paused_seconds, resolved_at FROM tickets "
                       "WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if row is None:
        return
    updates: dict = {}
    was_paused, now_paused = old in PAUSED_STATUSES, new in PAUSED_STATUSES
    if now_paused and not was_paused:
        updates["sla_paused_at"] = iso(moment)
    elif was_paused and not now_paused and row["sla_paused_at"]:
        calendar = _settings().calendar
        updates["sla_paused_seconds"] = int(row["sla_paused_seconds"] or 0) + int(
            working_seconds(parse(row["sla_paused_at"]), moment, calendar))
        updates["sla_paused_at"] = None
    if new in DONE_STATUSES and old not in DONE_STATUSES:
        updates["resolved_at"] = iso(moment)
    elif new not in DONE_STATUSES and old in DONE_STATUSES:
        updates["resolved_at"] = None
    if updates:
        conn.execute(f"UPDATE tickets SET {', '.join(f'{k} = ?' for k in updates)} WHERE ticket_id = ?",
                     (*updates.values(), ticket_id))


def note_first_response(conn, ticket_id: str, sender: str, author_id: str | None,
                        visibility: str, at: str) -> None:
    """The first public reply from support: a staff member, or the AI on a
    ticket it is handling (route ai/clarify)."""
    if visibility != "public_reply" or sender not in ("staff", "ai"):
        return
    if sender == "staff" and not author_id:
        return  # incident broadcast
    conn.execute(
        "UPDATE tickets SET first_response_at = ? WHERE ticket_id = ? AND first_response_at IS NULL"
        + (" AND route <> 'human'" if sender == "ai" else ""),
        (at, ticket_id),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _state(used: float, total: float, done: bool, at_risk: float) -> str:
    if done:
        return "met" if used <= total else "breached"
    if used > total:
        return "breached"
    return "at_risk" if total and used / total >= at_risk else "on_track"


def evaluate(ticket, at: datetime | None = None, settings=None) -> dict | None:
    """The SLA state of a ticket row (needs created_at, status,
    first_response_at, resolved_at and the sla_* columns). None when the
    ticket has no SLA."""
    if ticket["sla_resolution_minutes"] is None:
        return None
    settings = settings or _settings()
    at = at or now()
    calendar, risk = settings.calendar, settings.at_risk_percent / 100
    created = parse(ticket["created_at"])

    fr_total = ticket["sla_first_response_minutes"] * 60
    fr_at = parse(ticket["first_response_at"])
    fr_used = working_seconds(created, fr_at or at, calendar)
    first = {
        "target_minutes": ticket["sla_first_response_minutes"],
        "due_at": ticket["sla_first_response_due"],
        "done_at": iso(fr_at),
        "used_ratio": round(fr_used / fr_total, 3) if fr_total else None,
        "state": _state(fr_used, fr_total, fr_at is not None, risk),
    }

    res_total = ticket["sla_resolution_minutes"] * 60
    paused_at = parse(ticket["sla_paused_at"])
    done = ticket["status"] in DONE_STATUSES
    resolved = parse(ticket["resolved_at"])
    end = resolved if done and resolved else (paused_at or at)
    paused_so_far = int(ticket["sla_paused_seconds"] or 0)
    res_used = max(0.0, working_seconds(created, end, calendar) - paused_so_far)
    if paused_at and not done:
        state = "breached" if res_used > res_total else "paused"
    else:
        state = _state(res_used, res_total, done, risk)
    # The due time moves out by the time the clock was stopped.
    due = add_working_time(created, ticket["sla_resolution_minutes"], calendar) + timedelta(
        seconds=paused_so_far + (working_seconds(paused_at, at, calendar) if paused_at and not done else 0))
    resolution = {
        "target_minutes": ticket["sla_resolution_minutes"],
        "due_at": iso(due),
        "done_at": iso(resolved) if done else None,
        "used_ratio": round(res_used / res_total, 3) if res_total else None,
        "state": state,
        "paused": bool(paused_at) and not done,
    }
    states = {first["state"], resolution["state"]}
    worst = next(s for s in ("breached", "at_risk", "paused", "on_track", "met") if s in states)
    return {"first_response": first, "resolution": resolution,
            "within_sla": "breached" not in states, "state": worst}


SLA_COLUMNS = ("created_at, status, priority, first_response_at, resolved_at, sla_first_response_minutes, "
               "sla_resolution_minutes, sla_first_response_due, sla_resolution_due, sla_paused_at, "
               "sla_paused_seconds")
