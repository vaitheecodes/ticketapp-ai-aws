"""Admin reporting: the ticket dashboard and the SLA dashboard (admin role
only, enforced on every endpoint), and the priority change that
recalculates a ticket's SLA due times.

  GET  /api/admin/dashboard             ?since= &until= &priority=
  GET  /api/admin/sla                   ?since= &until= &priority=
  POST /api/admin/tickets/{id}/priority

Both dashboards cover the tickets created in [since, until] (dates or ISO
timestamps, UTC; default: the last 30 days), optionally one priority. The
numbers are aggregated in Python from one query, so the same code works on
SQLite and Postgres (timestamps come back as ISO text from both).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend import app_settings, auth, db, sla
from backend import ticket_lifecycle as lifecycle

router = APIRouter()

_admin = auth.get_current_admin

Priority = Literal["low", "medium", "high", "urgent"]
STATUSES = ("open", "in_progress", "pending_customer", "resolved", "closed")
DEFAULT_DAYS = 30
MAX_DAYS = 366


class PriorityRequest(BaseModel):
    priority: Priority


def _bound(value: str | None, end_of_day: bool) -> datetime | None:
    """A date ("2026-11-04") or ISO timestamp as an aware UTC datetime; a
    bare date covers the whole day."""
    if not value:
        return None
    try:
        if len(value) == 10:
            day = datetime.fromisoformat(value).date()
            return datetime.combine(day, time.max if end_of_day else time.min, tzinfo=timezone.utc)
        return sla.parse(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value}") from exc


def _range(since: str | None, until: str | None) -> tuple[datetime, datetime]:
    end = _bound(until, end_of_day=True) or sla.now()
    start = _bound(since, end_of_day=False) or (end - timedelta(days=DEFAULT_DAYS))
    if start > end:
        raise HTTPException(status_code=400, detail="The start date is after the end date")
    if end - start > timedelta(days=MAX_DAYS):
        raise HTTPException(status_code=400, detail=f"The range can be at most {MAX_DAYS} days")
    return start, end


def _tickets(start: datetime, end: datetime, priority: str | None) -> list[dict]:
    conn = db.get_connection()
    try:
        sql = ("SELECT t.ticket_id, t.subject, t.ticket_text, t.route, t.assigned_technician_id, "
               f"s.name AS technician_name, {', '.join('t.' + c.strip() for c in sla.SLA_COLUMNS.split(','))} "
               "FROM tickets t LEFT JOIN staff s ON s.staff_id = t.assigned_technician_id "
               "WHERE t.created_at >= ? AND t.created_at <= ?")
        params: list = [sla.iso(start), sla.iso(end)]
        if priority:
            sql += " AND t.priority = ?"
            params.append(priority)
        return [dict(r) for r in conn.execute(sql + " ORDER BY t.created_at", params)]
    finally:
        conn.close()


def _minutes(start, end) -> float | None:
    a, b = sla.parse(start), sla.parse(end)
    return (b - a).total_seconds() / 60 if a and b and b >= a else None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _is_open(ticket: dict) -> bool:
    return ticket["status"] not in sla.DONE_STATUSES


@router.get("/api/admin/dashboard")
def dashboard(since: str | None = Query(default=None, max_length=40),
              until: str | None = Query(default=None, max_length=40),
              priority: Priority | None = None,
              admin_id: str = Depends(_admin)):
    """Counts by status and priority, tickets created/resolved per day,
    open tickets by priority and by technician, and average first-response
    and resolution times (minutes) per priority."""
    start, end = _range(since, until)
    tickets = _tickets(start, end, priority)

    days = [(start.date() + timedelta(days=i)) for i in range((end.date() - start.date()).days + 1)]
    created = Counter(sla.parse(t["created_at"]).date() for t in tickets)
    resolved = Counter(sla.parse(t["resolved_at"]).date() for t in tickets
                       if t["resolved_at"] and not _is_open(t))

    open_tickets = [t for t in tickets if _is_open(t)]
    by_tech: dict[str | None, dict] = {}
    for t in open_tickets:
        key = t["assigned_technician_id"] if t["route"] == "human" else "ai"
        entry = by_tech.setdefault(key, {
            "technician_id": key if key not in (None, "ai") else None,
            "name": t["technician_name"] if key not in (None, "ai") else
            ("AI assistant" if key == "ai" else "Unassigned"),
            "count": 0})
        entry["count"] += 1

    times = {}
    for p in sla.PRIORITIES:
        rows = [t for t in tickets if t["priority"] == p]
        first = [m for t in rows if (m := _minutes(t["created_at"], t["first_response_at"])) is not None]
        resolution = [m for t in rows if not _is_open(t)
                      and (m := _minutes(t["created_at"], t["resolved_at"])) is not None]
        times[p] = {"tickets": len(rows),
                    "avg_first_response_minutes": _avg(first), "first_responses": len(first),
                    "avg_resolution_minutes": _avg(resolution), "resolved": len(resolution)}

    return {
        "since": sla.iso(start), "until": sla.iso(end), "priority": priority,
        "total": len(tickets),
        "by_status": {s: sum(1 for t in tickets if t["status"] == s) for s in STATUSES},
        "by_priority": {p: sum(1 for t in tickets if t["priority"] == p) for p in sla.PRIORITIES},
        "over_time": [{"date": d.isoformat(), "created": created.get(d, 0), "resolved": resolved.get(d, 0)}
                      for d in days],
        "open_by_priority": {p: sum(1 for t in open_tickets if t["priority"] == p) for p in sla.PRIORITIES},
        "open_by_technician": sorted(by_tech.values(), key=lambda e: (-e["count"], e["name"])),
        "response_times": times,
    }


def _subject(ticket: dict) -> str:
    if ticket["subject"]:
        return ticket["subject"]
    line = (ticket["ticket_text"] or "").split("\n", 1)[0].strip()
    return line if len(line) <= 80 else line[:77].rstrip() + "..."


@router.get("/api/admin/sla")
def sla_dashboard(since: str | None = Query(default=None, max_length=40),
                  until: str | None = Query(default=None, max_length=40),
                  priority: Priority | None = None,
                  admin_id: str = Depends(_admin)):
    """Per priority: compliance (met / (met + breached), for each target),
    breached and at-risk counts; and the open tickets at risk or past
    breach, soonest due first. Only tickets that got an SLA (created while
    it was on) count."""
    settings = app_settings.get("sla")
    start, end = _range(since, until)
    at = sla.now()
    evaluated = [(t, sla.evaluate(t, at, settings)) for t in _tickets(start, end, priority)]
    evaluated = [(t, e) for t, e in evaluated if e is not None]

    def compliance(results: list[str]) -> dict:
        met, breached = results.count("met"), results.count("breached")
        decided = met + breached
        return {"met": met, "breached": breached,
                "percent": round(100 * met / decided, 1) if decided else None}

    per_priority = {}
    for p in sla.PRIORITIES:
        rows = [e for t, e in evaluated if t["priority"] == p]
        per_priority[p] = {
            "tickets": len(rows),
            "first_response": compliance([e["first_response"]["state"] for e in rows]),
            "resolution": compliance([e["resolution"]["state"] for e in rows]),
            "breached": sum(1 for e in rows if not e["within_sla"]),
            "at_risk": sum(1 for e in rows if e["within_sla"] and e["state"] == "at_risk"),
            "within_sla": sum(1 for e in rows if e["within_sla"]),
        }

    def next_due(e: dict) -> str:
        running = [e[k]["due_at"] for k in ("first_response", "resolution")
                   if e[k]["state"] in ("at_risk", "breached") and not e[k]["done_at"]]
        return min(running) if running else e["resolution"]["due_at"]

    watch = sorted(
        ({"ticket_id": t["ticket_id"], "subject": _subject(t), "priority": t["priority"],
          "status": t["status"], "technician_name": t["technician_name"], "created_at": t["created_at"],
          "next_due_at": next_due(e), "sla": e}
         for t, e in evaluated if _is_open(t) and e["state"] in ("at_risk", "breached")),
        key=lambda r: sla.parse(r["next_due_at"]))
    return {
        "enabled": settings.enabled, "at_risk_percent": settings.at_risk_percent, "calendar": settings.calendar,
        "since": sla.iso(start), "until": sla.iso(end), "priority": priority,
        "tickets_with_sla": len(evaluated),
        "by_priority": per_priority,
        "totals": {
            "within_sla": sum(1 for _, e in evaluated if e["within_sla"]),
            "breached": sum(1 for _, e in evaluated if not e["within_sla"]),
            "at_risk": sum(1 for t, e in evaluated if e["within_sla"] and e["state"] == "at_risk"),
        },
        "watchlist": watch[:200],
    }


@router.post("/api/admin/tickets/{ticket_id}/priority")
def change_priority(ticket_id: str, body: PriorityRequest, admin_id: str = Depends(_admin)):
    """Set the priority; the SLA due times are recalculated from creation."""
    conn = db.get_connection()
    try:
        if conn.execute("SELECT 1 FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        lifecycle.set_priority(conn, ticket_id, body.priority, admin_id)
        conn.commit()
        row = conn.execute(f"SELECT {sla.SLA_COLUMNS} FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
        return {"ticket_id": ticket_id, "priority": row["priority"], "sla": sla.evaluate(row)}
    finally:
        conn.close()
