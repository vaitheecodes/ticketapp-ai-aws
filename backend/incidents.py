"""AI incident correlation with manager review.

1. Candidates: tickets created in the last INCIDENT_WINDOW_HOURS (not in a
   dismissed cluster), grouped by component area — the department that
   owns the ticket's category (so e.g. AGENT and SERVER_MONITORING reports
   of one outage land together; the classifier often splits them), or the
   category itself if no department owns it — and, when an embeddings
   deployment is configured, split by embedding similarity
   (INCIDENT_SIMILARITY). A group only goes to the AI if it has at least
   INCIDENT_MIN_TICKETS tickets from at least two customers: a cheap
   pre-filter to limit model calls, not a decision.
2. Judgement: ai_service.correlate_incidents decides whether (and which)
   tickets plausibly share an incident, with facts kept apart from
   hypotheses. Similar wording alone isn't evidence.
3. Dedupe: an open cluster that already holds any of the related tickets
   is updated instead of creating a duplicate. A group with nothing new
   isn't re-sent to the model.
4. Tickets are only linked — never merged, closed or deleted.

Department routing: departments owning the tickets' categories plus the
AI's candidates, but only when the AI's confidence reaches
INCIDENT_ROUTING_CONFIDENCE; otherwise the cluster is "Needs routing" for
the admin or a manager to assign. Manager corrections are never
overwritten by later AI runs.

Correlation runs in the background after every new ticket or chat
escalation (run_for_ticket), and on demand (POST /api/incidents/run).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend import auth, db
from backend.ai_service import AIError, correlate_incidents, provider, summarize_incident
from backend.ai_service.schemas import (
    CorrelateInput,
    CorrelationJudgement,
    CorrelationTicket,
    DepartmentOption,
    SummarizeIncidentInput,
)
from backend.conversation import ESCALATION_MARKER, now, subject_for

logger = logging.getLogger(__name__)

router = APIRouter()

OPEN_STATUSES = ("detected", "under_investigation", "confirmed")
CUSTOMER_NOTICE = "We're aware of this issue affecting multiple customers and are working on it."


# ---------------------------------------------------------------------------
# Settings (read at call time so tests and the admin console can change them)
# ---------------------------------------------------------------------------
def _env_number(name: str, default, cast):
    try:
        return cast(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning("Invalid %s; using default %s", name, default)
        return default


def settings() -> dict:
    return {
        "window_hours": _env_number("INCIDENT_WINDOW_HOURS", 12, int),
        "min_tickets": max(2, _env_number("INCIDENT_MIN_TICKETS", 3, int)),
        "similarity": _env_number("INCIDENT_SIMILARITY", 0.80, float),
        "routing_confidence": _env_number("INCIDENT_ROUTING_CONFIDENCE", 0.6, float),
    }


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------
def _public_details(ticket) -> str:
    """What the model may see: the customer's own opening text (no case
    summary, internal notes or staff events)."""
    return ticket["ticket_text"].split(f"\n\n{ESCALATION_MARKER}\n", 1)[0][:3000]


def _eligible_tickets(conn, window_hours: int) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    return conn.execute(
        "SELECT t.*, COALESCE(t.category, t.requested_category) AS group_category FROM tickets t "
        "WHERE t.created_at >= ? AND COALESCE(t.category, t.requested_category) IS NOT NULL "
        "AND t.ticket_id NOT IN (SELECT it.ticket_id FROM incident_tickets it "
        "  JOIN incident_clusters c ON c.incident_id = it.incident_id WHERE c.status = 'dismissed') "
        "ORDER BY t.created_at",
        (cutoff,),
    ).fetchall()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _embeddings(conn, tickets) -> dict[str, list[float]] | None:
    """Cached per ticket; None when no embeddings deployment is configured
    or the call fails (callers fall back to category-only grouping)."""
    model = provider.embeddings_deployment()
    if not model:
        return None
    vectors, missing = {}, []
    for t in tickets:
        text = f"{subject_for(t)}\n{_public_details(t)}"
        digest = hashlib.sha256(text.encode()).hexdigest()
        row = conn.execute(
            "SELECT vector FROM ticket_embeddings WHERE ticket_id = ? AND model = ? AND text_hash = ?",
            (t["ticket_id"], model, digest),
        ).fetchone()
        if row:
            vectors[t["ticket_id"]] = json.loads(row["vector"])
        else:
            missing.append((t["ticket_id"], digest, text))
    if missing:
        try:
            fresh = provider.embed([text for _, _, text in missing])
        except AIError:
            return None
        for (ticket_id, digest, _), vector in zip(missing, fresh):
            vectors[ticket_id] = vector
            conn.execute(
                "INSERT OR REPLACE INTO ticket_embeddings (ticket_id, model, text_hash, vector) "
                "VALUES (?, ?, ?, ?)",
                (ticket_id, model, digest, json.dumps(vector)),
            )
    return vectors


def _split_by_similarity(tickets, vectors, threshold: float) -> list[list]:
    """Greedy single-link grouping: a ticket joins the first group that has
    a member at least `threshold` similar to it."""
    groups: list[list] = []
    for t in tickets:
        for group in groups:
            if any(_cosine(vectors[t["ticket_id"]], vectors[m["ticket_id"]]) >= threshold for m in group):
                group.append(t)
                break
        else:
            groups.append([t])
    return groups


def candidate_groups(conn, cfg: dict, trigger_ticket_id: str | None = None) -> list[list]:
    _, _, owner_of = _department_options(conn)
    by_area: dict[str, list] = {}
    for t in _eligible_tickets(conn, cfg["window_hours"]):
        area = owner_of.get(t["group_category"], f"category:{t['group_category']}")
        by_area.setdefault(area, []).append(t)

    groups = []
    for tickets in by_area.values():
        if trigger_ticket_id and trigger_ticket_id not in {t["ticket_id"] for t in tickets}:
            continue
        if len(tickets) < cfg["min_tickets"]:
            continue
        vectors = _embeddings(conn, tickets)
        subgroups = _split_by_similarity(tickets, vectors, cfg["similarity"]) if vectors else [tickets]
        for group in subgroups:
            if trigger_ticket_id and trigger_ticket_id not in {t["ticket_id"] for t in group}:
                continue
            if len(group) >= cfg["min_tickets"] and len({t["customer_id"] for t in group}) >= 2:
                groups.append(group[-40:])  # newest 40: the model input cap
    return groups


# ---------------------------------------------------------------------------
# Judgement, dedupe, routing
# ---------------------------------------------------------------------------
def _department_options(conn) -> tuple[list[DepartmentOption], dict[str, str], dict[str, str]]:
    options, by_name, owner_of = [], {}, {}
    for row in conn.execute("SELECT department_id, name, categories FROM departments"):
        categories = json.loads(row["categories"])
        options.append(DepartmentOption(name=row["name"], categories=categories))
        by_name[row["name"]] = row["department_id"]
        for c in categories:
            owner_of[c] = row["department_id"]
    return options, by_name, owner_of


def _correlation_input(tickets, departments, window_hours: int) -> CorrelateInput:
    refs: dict[str, str] = {}
    items = []
    for t in tickets:
        refs.setdefault(t["customer_id"], f"C{len(refs) + 1}")
        items.append(CorrelationTicket(
            ticket_id=t["ticket_id"], customer_ref=refs[t["customer_id"]], created_at=t["created_at"],
            category=t["group_category"], subject=subject_for(t), details=_public_details(t),
        ))
    return CorrelateInput(tickets=items, departments=departments, window_hours=window_hours)


def _open_cluster_for(conn, ticket_ids: set[str]):
    if not ticket_ids:
        return None
    marks = ",".join("?" * len(ticket_ids))
    return conn.execute(
        f"SELECT c.* FROM incident_clusters c JOIN incident_tickets it ON it.incident_id = c.incident_id "
        f"WHERE it.ticket_id IN ({marks}) AND c.status IN ('detected', 'under_investigation', 'confirmed') "
        "ORDER BY c.updated_at DESC LIMIT 1",
        tuple(ticket_ids),
    ).fetchone()


def _cluster_ticket_ids(conn, incident_id: str) -> set[str]:
    return {r["ticket_id"] for r in conn.execute(
        "SELECT ticket_id FROM incident_tickets WHERE incident_id = ?", (incident_id,))}


def _narrative(input_: CorrelateInput, judgement: CorrelationJudgement) -> dict:
    related = [t for t in input_.tickets if t.ticket_id in judgement.related_ticket_ids]
    try:
        summary = summarize_incident.summarize_incident(SummarizeIncidentInput(
            title=judgement.title, tickets=related,
            observed_facts=judgement.observed_facts, hypotheses=judgement.hypotheses,
        ))
        return {"title": summary.title, "narrative": summary.summary}
    except AIError:
        return {"title": judgement.title, "narrative": judgement.why_related}


def _route(conn, incident_id: str, judgement: CorrelationJudgement, tickets, by_name, owner_of,
           cfg) -> None:
    """Replace the AI-proposed departments (manager-set ones are kept).
    Below the routing confidence bar the AI proposes none."""
    conn.execute(
        "DELETE FROM incident_departments WHERE incident_id = ? AND source = 'ai'", (incident_id,))
    proposed: set[str] = set()
    if judgement.confidence >= cfg["routing_confidence"]:
        related = {tid for tid in judgement.related_ticket_ids}
        proposed |= {owner_of[t["group_category"]] for t in tickets
                     if t["ticket_id"] in related and t["group_category"] in owner_of}
        proposed |= {by_name[name] for name in judgement.candidate_departments if name in by_name}
    for department_id in proposed:
        conn.execute(
            "INSERT OR IGNORE INTO incident_departments (incident_id, department_id, source, confirmed) "
            "VALUES (?, ?, 'ai', 0)",
            (incident_id, department_id),
        )
    _refresh_needs_routing(conn, incident_id)


def _refresh_needs_routing(conn, incident_id: str) -> None:
    has_department = conn.execute(
        "SELECT 1 FROM incident_departments WHERE incident_id = ?", (incident_id,)).fetchone()
    conn.execute("UPDATE incident_clusters SET needs_routing = ? WHERE incident_id = ?",
                 (0 if has_department else 1, incident_id))


def _process_group(conn, tickets, cfg) -> str | None:
    group_ids = {t["ticket_id"] for t in tickets}
    existing = _open_cluster_for(conn, group_ids)
    if existing and group_ids <= _cluster_ticket_ids(conn, existing["incident_id"]):
        return None  # nothing new for the model to judge

    departments, by_name, owner_of = _department_options(conn)
    input_ = _correlation_input(tickets, departments, cfg["window_hours"])
    judgement = correlate_incidents.correlate_incidents(input_)
    related = set(judgement.related_ticket_ids)
    customers = {t["customer_id"] for t in tickets if t["ticket_id"] in related}
    if not judgement.is_related or len(related) < 2 or len(customers) < 2:
        return None
    narrative = _narrative(input_, judgement)
    summary = json.dumps({**judgement.model_dump(), **narrative})

    # Serialise writers: a concurrent run must see this cluster, not
    # create a second one for the same event.
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = _open_cluster_for(conn, related)
        stamp = now()
        if existing:
            incident_id = existing["incident_id"]
            conn.execute(
                "UPDATE incident_clusters SET title = ?, summary = ?, confidence = ?, updated_at = ? "
                "WHERE incident_id = ?",
                (narrative["title"], summary, judgement.confidence, stamp, incident_id),
            )
        else:
            incident_id = f"INC-{uuid.uuid4().hex[:8]}"
            # The most reported category among the related tickets.
            categories = [t["group_category"] for t in tickets if t["ticket_id"] in related]
            category = max(set(categories), key=categories.count)
            conn.execute(
                "INSERT INTO incident_clusters (incident_id, title, status, category, summary, "
                "confidence, needs_routing, created_at, updated_at) VALUES (?, ?, 'detected', ?, ?, ?, 1, ?, ?)",
                (incident_id, narrative["title"], category, summary, judgement.confidence, stamp, stamp),
            )
        conn.executemany(
            "INSERT OR IGNORE INTO incident_tickets (incident_id, ticket_id, added_at) VALUES (?, ?, ?)",
            [(incident_id, tid, stamp) for tid in sorted(related)],
        )
        _route(conn, incident_id, judgement, tickets, by_name, owner_of, cfg)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return incident_id


def run_correlation(trigger_ticket_id: str | None = None) -> list[str]:
    """One correlation pass. AI failures skip that group (nothing is
    created from a failed judgement). Returns the clusters created or
    updated."""
    cfg = settings()
    conn = db.get_connection()
    touched = []
    try:
        for group in candidate_groups(conn, cfg, trigger_ticket_id):
            try:
                incident_id = _process_group(conn, group, cfg)
            except AIError:
                logger.warning("Incident correlation skipped a group: AI unavailable")
                continue
            if incident_id:
                touched.append(incident_id)
        conn.commit()
    finally:
        conn.close()
    return touched


def run_for_ticket(ticket_id: str) -> None:
    """Background task after a new ticket or chat escalation."""
    try:
        run_correlation(ticket_id)
    except Exception:  # noqa: BLE001 — never fail the request that queued it
        logger.exception("Incident correlation failed after %s", ticket_id)


def confirmed_incident_for(conn, ticket_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM incident_tickets it JOIN incident_clusters c ON c.incident_id = it.incident_id "
        "WHERE it.ticket_id = ? AND c.status = 'confirmed'", (ticket_id,)
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
_reviewer = auth.require_user("manager", "admin")


def manager_department_ids(conn, manager_id: str) -> set[str]:
    return {r["department_id"] for r in conn.execute(
        "SELECT department_id FROM manager_departments WHERE manager_id = ?", (manager_id,))}


def _cluster_departments(conn, incident_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT d.department_id, d.name, idp.source, idp.confirmed FROM incident_departments idp "
        "JOIN departments d ON d.department_id = idp.department_id WHERE idp.incident_id = ? "
        "ORDER BY d.name", (incident_id,))]


def _can_see(conn, cluster, user: dict) -> bool:
    """Admin sees every cluster. A manager sees clusters linked to one of
    their departments, plus unrouted ("Needs routing") ones so they can
    claim them for the right department."""
    if user["role"] == "admin":
        return True
    if cluster["needs_routing"]:
        return True
    mine = manager_department_ids(conn, user["id"])
    return any(d["department_id"] in mine for d in _cluster_departments(conn, cluster["incident_id"]))


def manager_can_see_ticket(conn, manager_id: str, ticket_id: str) -> bool:
    """Managers may open any ticket linked to a cluster they can see."""
    mine = manager_department_ids(conn, manager_id)
    for row in conn.execute(
        "SELECT c.* FROM incident_clusters c JOIN incident_tickets it ON it.incident_id = c.incident_id "
        "WHERE it.ticket_id = ?", (ticket_id,)
    ):
        if row["needs_routing"] or any(
            d["department_id"] in mine for d in _cluster_departments(conn, row["incident_id"])
        ):
            return True
    return False


def _cluster_or_404(conn, incident_id: str, user: dict):
    cluster = conn.execute(
        "SELECT * FROM incident_clusters WHERE incident_id = ?", (incident_id,)).fetchone()
    if cluster is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not _can_see(conn, cluster, user):
        raise HTTPException(status_code=403, detail="Incident is outside your departments")
    return cluster


def _cluster_row(conn, cluster) -> dict:
    stats = conn.execute(
        "SELECT COUNT(*) AS tickets, COUNT(DISTINCT t.customer_id) AS customers, "
        "MIN(t.created_at) AS first_report, MAX(t.created_at) AS latest_report "
        "FROM incident_tickets it JOIN tickets t ON t.ticket_id = it.ticket_id WHERE it.incident_id = ?",
        (cluster["incident_id"],),
    ).fetchone()
    return {
        "incident_id": cluster["incident_id"],
        "title": cluster["title"],
        "status": cluster["status"],
        "category": cluster["category"],
        "confidence": cluster["confidence"],
        "needs_routing": bool(cluster["needs_routing"]),
        "created_at": cluster["created_at"],
        "updated_at": cluster["updated_at"],
        "ticket_count": stats["tickets"],
        "customer_count": stats["customers"],
        "first_report": stats["first_report"],
        "latest_report": stats["latest_report"],
        "departments": _cluster_departments(conn, cluster["incident_id"]),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
class ReviewRequest(BaseModel):
    action: Literal["confirm", "dismiss", "investigate"]
    note: str = Field(min_length=1, max_length=2000)


class IncidentDepartmentsRequest(BaseModel):
    department_ids: list[str]


_STATUS_FOR_ACTION = {"confirm": "confirmed", "dismiss": "dismissed", "investigate": "under_investigation"}
_STATUS_ORDER = {"detected": 0, "under_investigation": 1, "confirmed": 2, "dismissed": 3}


@router.get("/api/incidents")
def list_incidents(include_dismissed: bool = False, user: dict = Depends(_reviewer)):
    """Prioritised: open before dismissed, then most confident, then most
    recent."""
    conn = db.get_connection()
    try:
        rows = [
            _cluster_row(conn, c)
            for c in conn.execute("SELECT * FROM incident_clusters")
            if (include_dismissed or c["status"] != "dismissed") and _can_see(conn, c, user)
        ]
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        rows.sort(key=lambda r: (_STATUS_ORDER[r["status"]] == 3, -r["confidence"]))
        return rows
    finally:
        conn.close()


@router.get("/api/incidents/{incident_id}")
def incident_detail(incident_id: str, user: dict = Depends(_reviewer)):
    conn = db.get_connection()
    try:
        cluster = _cluster_or_404(conn, incident_id, user)
        tickets = [
            {"ticket_id": t["ticket_id"], "subject": subject_for(t), "status": t["status"],
             "priority": t["priority"], "created_at": t["created_at"], "customer_name": t["customer_name"],
             "category": t["category"] or t["requested_category"], "added_at": t["added_at"]}
            for t in conn.execute(
                "SELECT t.*, c.name AS customer_name, it.added_at FROM incident_tickets it "
                "JOIN tickets t ON t.ticket_id = it.ticket_id JOIN customers c ON c.customer_id = t.customer_id "
                "WHERE it.incident_id = ? ORDER BY t.created_at", (incident_id,))
        ]
        names = {r["id"]: r["name"] for r in conn.execute(
            "SELECT staff_id AS id, name FROM staff UNION ALL SELECT admin_id, name FROM admins")}
        reviews = [
            {**dict(r), "reviewer_name": names.get(r["reviewer"], r["reviewer"])}
            for r in conn.execute(
                "SELECT reviewer, action, note, at FROM incident_reviews WHERE incident_id = ? ORDER BY id",
                (incident_id,))
        ]
        return {
            **_cluster_row(conn, cluster),
            "summary": json.loads(cluster["summary"]),
            "tickets": tickets,
            "affected_customers": sorted({t["customer_name"] for t in tickets}),
            "reviews": reviews,
            "all_departments": [dict(r) for r in conn.execute(
                "SELECT department_id, name FROM departments ORDER BY name")],
        }
    finally:
        conn.close()


@router.post("/api/incidents/{incident_id}/review")
def review_incident(incident_id: str, body: ReviewRequest, user: dict = Depends(_reviewer)):
    """Confirm, dismiss or mark under investigation, with a note. Changes
    only the cluster: linked tickets are never closed or merged."""
    conn = db.get_connection()
    try:
        _cluster_or_404(conn, incident_id, user)
        conn.execute(
            "UPDATE incident_clusters SET status = ?, updated_at = ? WHERE incident_id = ?",
            (_STATUS_FOR_ACTION[body.action], now(), incident_id),
        )
        conn.execute(
            "INSERT INTO incident_reviews (incident_id, reviewer, action, note, at) VALUES (?, ?, ?, ?, ?)",
            (incident_id, user["id"], body.action, body.note.strip(), now()),
        )
        conn.commit()
    finally:
        conn.close()
    return incident_detail(incident_id, user)


@router.put("/api/incidents/{incident_id}/departments")
def set_incident_departments(
    incident_id: str, body: IncidentDepartmentsRequest, user: dict = Depends(_reviewer)
):
    """Correct the department association: replaces every link with
    confirmed ones (source 'manager') that later AI runs won't touch. A
    manager must keep at least one of their own departments on it; only
    the admin can clear it back to Needs routing."""
    conn = db.get_connection()
    try:
        _cluster_or_404(conn, incident_id, user)
        wanted = set(body.department_ids)
        known = {r["department_id"]: r["name"] for r in conn.execute(
            "SELECT department_id, name FROM departments")}
        if not wanted <= known.keys():
            raise HTTPException(status_code=404, detail="Unknown department")
        if user["role"] == "manager" and not wanted & manager_department_ids(conn, user["id"]):
            raise HTTPException(
                status_code=400, detail="Keep at least one of your departments, or ask the admin to reroute it")
        conn.execute("DELETE FROM incident_departments WHERE incident_id = ?", (incident_id,))
        conn.executemany(
            "INSERT INTO incident_departments (incident_id, department_id, source, confirmed) "
            "VALUES (?, ?, 'manager', 1)",
            [(incident_id, d) for d in sorted(wanted)],
        )
        _refresh_needs_routing(conn, incident_id)
        names = sorted(known[d] for d in wanted)
        conn.execute(
            "INSERT INTO incident_reviews (incident_id, reviewer, action, note, at) VALUES (?, ?, ?, ?, ?)",
            (incident_id, user["id"], "departments_changed", ", ".join(names) or "Needs routing", now()),
        )
        conn.commit()
    finally:
        conn.close()
    return incident_detail(incident_id, user)


@router.post("/api/incidents/run")
def run_now(user: dict = Depends(_reviewer)):
    """Run a correlation pass now (managers and admin)."""
    return {"touched": run_correlation()}
