"""Resolving an incident and sending the post-incident summary.

  1. Draft  (POST .../resolution/draft)  — only a CONFIRMED incident. Staff
     give the resolution note, when the fix was applied (IST if no offset)
     and an optional root cause. The AI drafts a customer summary and an
     internal one from the confirmed incident's facts and that note only
     (ai_service.draft_post_incident). If the AI fails, the staff input is
     kept and staff can write the summary themselves; nothing is sent.
  2. Edit   (PUT .../resolution/summary) — staff edit the draft and pick
     the visibility (affected_only | public).
  3. Approve & send (POST .../resolution/send) — never automatic. Posts the
     approved text as a public email-style reply from the Support Team on
     every linked ticket (and emails it when EMAIL_ENABLED=true). One
     transaction per ticket with a delivery row: sent tickets are never
     re-posted (idempotent), a failing ticket is recorded and the others
     continue, and sending again retries only the failures. Optionally
     marks the linked tickets Resolved (off by default); nothing is ever
     closed or merged. The incident becomes resolved / sent and is
     remembered (backend/incident_memory.py).

Who: a manager who can see the incident, the admin, or the technician the
incident is assigned to. Every step is in the audit log.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from backend import audit, auth, db, incident_memory, incidents
from backend import ticket_lifecycle as lifecycle
from backend.ai_service import AIError, draft_post_incident
from backend.ai_service.schemas import PostIncidentFacts
from backend.conversation import Outbox, customer_row, insert_ticket_message, now
from backend.timeutil import format_ist, parse, to_utc_iso, utcnow

logger = logging.getLogger(__name__)

router = APIRouter()

_staff = auth.require_user("technician", "manager", "admin")

UPDATE_SUBJECT = "Update on a service issue you reported"
AI_UNAVAILABLE = (
    "The AI couldn't draft the summary right now. Your resolution details are saved — "
    "write the summary yourself below, or try drafting again."
)


class DraftRequest(BaseModel):
    resolution_note: str = Field(min_length=1, max_length=4000)
    fixed_at: str = Field(min_length=10, max_length=40)  # ISO; no offset = IST
    root_cause: str | None = Field(default=None, max_length=2000)


class SummaryRequest(BaseModel):
    customer_summary: str = Field(min_length=1, max_length=6000)
    internal_summary: str | None = Field(default=None, max_length=6000)
    visibility: Literal["affected_only", "public"] | None = None


class SendRequest(BaseModel):
    """customer_summary: the exact text being approved (must match what's
    stored, so staff approve what they last saw). Ignored on a retry."""

    customer_summary: str = Field(min_length=1, max_length=6000)
    mark_tickets_resolved: bool = False


class VisibilityRequest(BaseModel):
    visibility: Literal["affected_only", "public"]


def _resolvable(conn, incident_id: str, user: dict):
    cluster = incidents.cluster_or_404(conn, incident_id, user)
    if not incidents.can_resolve(cluster, user, conn):
        raise HTTPException(status_code=403, detail="Only a manager or the assigned technician can resolve this")
    return cluster


def _facts(conn, cluster, resolved_at: str, note: str, root_cause: str | None) -> PostIncidentFacts:
    analysis = json.loads(cluster["summary"])
    counts = conn.execute(
        "SELECT COUNT(*) AS tickets, COUNT(DISTINCT t.customer_id) AS customers FROM incident_tickets it "
        "JOIN tickets t ON t.ticket_id = it.ticket_id WHERE it.incident_id = ?",
        (cluster["incident_id"],),
    ).fetchone()
    return PostIncidentFacts(
        title=cluster["title"],
        category=cluster["category"],
        started_at_ist=format_ist(cluster["started_at"]),
        resolved_at_ist=format_ist(resolved_at),
        common_symptoms=analysis.get("common_symptoms") or [],
        affected_services=analysis.get("affected_services") or [],
        observed_facts=analysis.get("observed_facts") or [],
        resolution_note=note,
        root_cause=root_cause,
        affected_customer_count=counts["customers"],
        affected_ticket_count=counts["tickets"],
    )


@router.post("/api/incidents/{incident_id}/resolution/draft")
def draft_resolution(incident_id: str, body: DraftRequest, user: dict = Depends(_staff)):
    conn = db.get_connection()
    try:
        cluster = _resolvable(conn, incident_id, user)
        if cluster["status"] != "confirmed":
            raise HTTPException(status_code=400, detail="Only a confirmed incident can be resolved")
        try:
            fixed_at = parse(body.fixed_at)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="fixed_at must be a date and time") from exc
        if fixed_at > utcnow() + timedelta(minutes=5):
            raise HTTPException(status_code=400, detail="The fix time can't be in the future")
        if cluster["started_at"] and fixed_at < parse(cluster["started_at"]):
            raise HTTPException(status_code=400, detail="The fix time is before the incident started")

        resolved_at = to_utc_iso(fixed_at)
        note = body.resolution_note.strip()
        root_cause = (body.root_cause or "").strip() or None
        conn.execute(
            "UPDATE incident_clusters SET resolved_at = ?, resolution_note = ?, root_cause = ?, "
            "updated_at = ? WHERE incident_id = ?",
            (resolved_at, note, root_cause, now(), incident_id),
        )
        ai_error = None
        try:
            customer_summary, internal_summary = draft_post_incident.draft_post_incident(
                _facts(conn, cluster, resolved_at, note, root_cause)
            )
            conn.execute(
                "UPDATE incident_clusters SET customer_summary = ?, internal_summary = ?, "
                "summary_status = 'draft' WHERE incident_id = ?",
                (customer_summary, internal_summary, incident_id),
            )
        except AIError:
            ai_error = AI_UNAVAILABLE
        audit.record(conn, user["id"], "incident.summary_drafted", "incident", incident_id,
                     source="failed" if ai_error else "ai", root_cause_given=bool(root_cause))
        conn.commit()
    finally:
        conn.close()
    return {**incidents.incident_detail(incident_id, user), "ai_error": ai_error}


@router.put("/api/incidents/{incident_id}/resolution/summary")
def edit_summary(incident_id: str, body: SummaryRequest, user: dict = Depends(_staff)):
    conn = db.get_connection()
    try:
        cluster = _resolvable(conn, incident_id, user)
        if cluster["status"] != "confirmed" or cluster["summary_status"] == "sent":
            raise HTTPException(status_code=400, detail="The summary can only be edited before it's sent")
        if not cluster["resolution_note"]:
            raise HTTPException(status_code=400, detail="Enter the resolution details first")
        updates = {"customer_summary": body.customer_summary.strip(), "summary_status": "draft"}
        if body.internal_summary is not None:
            updates["internal_summary"] = body.internal_summary.strip()
        if body.visibility:
            updates["visibility"] = body.visibility
        conn.execute(
            f"UPDATE incident_clusters SET {', '.join(f'{k} = ?' for k in updates)}, updated_at = ? "
            "WHERE incident_id = ?",
            (*updates.values(), now(), incident_id),
        )
        audit.record(conn, user["id"], "incident.summary_edited", "incident", incident_id,
                     fields=sorted(k for k in updates if k != "summary_status"))
        conn.commit()
    finally:
        conn.close()
    return incidents.incident_detail(incident_id, user)


def post_update(conn, incident_id: str, ticket, text: str) -> str:
    """The incident update on one ticket's thread, from the Support Team
    (sender 'staff', no author). Separate so tests can make one fail."""
    return insert_ticket_message(conn, ticket["ticket_id"], "staff", text, subject=UPDATE_SUBJECT)


def _deliver(conn, incident_id: str, ticket_id: str, text: str, mark_resolved: bool,
             actor: str, outbox: Outbox) -> str:
    """One ticket, one transaction. Returns 'sent', 'skipped' or 'failed'."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        done = conn.execute(
            "SELECT 1 FROM incident_deliveries WHERE incident_id = ? AND ticket_id = ? AND status = 'sent'",
            (incident_id, ticket_id),
        ).fetchone()
        if done:
            conn.rollback()
            return "skipped"
        ticket = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
        message_id = post_update(conn, incident_id, ticket, text)
        if mark_resolved and ticket["status"] not in ("resolved", "closed"):
            lifecycle.set_status(conn, ticket_id, "resolved", actor, event=True)
        conn.execute(
            "INSERT INTO incident_deliveries (incident_id, ticket_id, status, message_id, error, attempted_at) "
            "VALUES (?, ?, 'sent', ?, NULL, ?) ON CONFLICT(incident_id, ticket_id) DO UPDATE SET "
            "status = 'sent', message_id = excluded.message_id, error = NULL, attempted_at = excluded.attempted_at",
            (incident_id, ticket_id, message_id, now()),
        )
        conn.commit()
        outbox.append((customer_row(conn, ticket["customer_id"])["email"], UPDATE_SUBJECT, text))
        return "sent"
    except Exception as exc:  # noqa: BLE001 — record and move on to the next ticket
        conn.rollback()
        logger.exception("Incident update failed for %s on %s", incident_id, ticket_id)
        conn.execute(
            "INSERT INTO incident_deliveries (incident_id, ticket_id, status, message_id, error, attempted_at) "
            "VALUES (?, ?, 'failed', NULL, ?, ?) ON CONFLICT(incident_id, ticket_id) DO UPDATE SET "
            "status = 'failed', error = excluded.error, attempted_at = excluded.attempted_at",
            (incident_id, ticket_id, type(exc).__name__, now()),
        )
        conn.commit()
        return "failed"


@router.post("/api/incidents/{incident_id}/resolution/send")
def approve_and_send(incident_id: str, body: SendRequest, background_tasks: BackgroundTasks,
                     user: dict = Depends(_staff)):
    outbox = Outbox()
    conn = db.get_connection()
    try:
        cluster = _resolvable(conn, incident_id, user)
        retry = cluster["status"] == "resolved" and cluster["summary_status"] == "sent"
        if not retry:
            if cluster["status"] != "confirmed":
                raise HTTPException(status_code=400, detail="Only a confirmed incident can be resolved")
            if not (cluster["resolution_note"] and cluster["resolved_at"]):
                raise HTTPException(status_code=400, detail="Enter the resolution details first")
            if not (cluster["customer_summary"] or "").strip():
                raise HTTPException(status_code=400, detail="There's no summary to send")
            if body.customer_summary.strip() != cluster["customer_summary"].strip():
                raise HTTPException(
                    status_code=409, detail="The summary changed since you reviewed it — save or reload first")
            conn.execute(
                "UPDATE incident_clusters SET summary_status = 'approved', updated_at = ? WHERE incident_id = ?",
                (now(), incident_id),
            )
            audit.record(conn, user["id"], "incident.summary_approved", "incident", incident_id)
            conn.commit()

        text = cluster["customer_summary"].strip()
        ticket_ids = [r["ticket_id"] for r in conn.execute(
            "SELECT ticket_id FROM incident_tickets WHERE incident_id = ? ORDER BY ticket_id", (incident_id,))]
        results = {
            tid: _deliver(conn, incident_id, tid, text, body.mark_tickets_resolved, user["id"], outbox)
            for tid in ticket_ids
        }
        failed = sorted(t for t, r in results.items() if r == "failed")
        conn.execute(
            "UPDATE incident_clusters SET status = 'resolved', summary_status = 'sent', "
            "resolved_by = COALESCE(resolved_by, ?), updated_at = ? WHERE incident_id = ?",
            (user["id"], now(), incident_id),
        )
        incident_memory.remember(conn, incident_id)
        audit.record(conn, user["id"], "incident.summary_sent", "incident", incident_id,
                     sent=sum(r == "sent" for r in results.values()),
                     already_sent=sum(r == "skipped" for r in results.values()),
                     failed_tickets=failed, marked_resolved=body.mark_tickets_resolved, retry=retry)
        if not retry:
            audit.record(conn, user["id"], "incident.resolved", "incident", incident_id)
        conn.commit()
    finally:
        conn.close()
    outbox.flush(background_tasks)  # real email only when EMAIL_ENABLED=true
    return incidents.incident_detail(incident_id, user)


@router.put("/api/incidents/{incident_id}/visibility")
def set_visibility(incident_id: str, body: VisibilityRequest, user: dict = Depends(incidents._reviewer)):
    """affected_only (default): only customers with a linked ticket hear
    about it. public: anyone asking, and the public incident history."""
    conn = db.get_connection()
    try:
        incidents.cluster_or_404(conn, incident_id, user)
        conn.execute(
            "UPDATE incident_clusters SET visibility = ?, updated_at = ? WHERE incident_id = ?",
            (body.visibility, now(), incident_id),
        )
        incident_memory.remember(conn, incident_id)
        audit.record(conn, user["id"], "incident.visibility_changed", "incident", incident_id,
                     visibility=body.visibility)
        conn.commit()
    finally:
        conn.close()
    return incidents.incident_detail(incident_id, user)


@router.get("/api/public/incidents")
def public_incident_history():
    """No login. Public incidents only: status, times and the approved
    summary — nothing about customers or tickets."""
    conn = db.get_connection()
    try:
        return incident_memory.public_history(conn)
    finally:
        conn.close()



