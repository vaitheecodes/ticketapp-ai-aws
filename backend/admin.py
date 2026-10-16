"""Admin console API (admin role only, enforced on every endpoint).

  Tickets      all tickets, technician list, reassign, pipeline simulator
  Team         technicians and managers: create, edit (name, email, role,
               the manager's one department), deactivate, reset password
  Customers    create, edit, deactivate, reset password
  Departments  names and KB-category mapping (a category belongs to at
               most one department)
  Settings     routing policy, incident correlation, status labels
               (backend/app_settings.py)
  Audit log    filterable view of audit_events

Every change here is written to the audit log (backend/audit.py) with
field names and ids only — never passwords.
"""

from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from backend import app_settings, audit, auth, db, graph, runtime
from backend import ticket_lifecycle as lifecycle
from backend.ai_service import AIError
from backend.conversation import insert_ticket_message
from backend.email_utils import clean_email

router = APIRouter()

_admin = auth.get_current_admin


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SimulateTicketRequest(BaseModel):
    ticket_text: str
    channel: str = "chat"
    customer_id: str | None = None


class ReassignRequest(BaseModel):
    technician_id: str | None = None


class CreateStaffRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    role: Literal["technician", "manager"]
    temporary_password: str
    department_id: str | None = None


class UpdateStaffRequest(BaseModel):
    """Only the fields present are changed. department_id applies to
    managers; send null to clear it."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, min_length=3, max_length=254)
    role: Literal["technician", "manager"] | None = None
    department_id: str | None = None


class CreateCustomerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    temporary_password: str


class UpdateCustomerRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, min_length=3, max_length=254)


class ResetPasswordRequest(BaseModel):
    temporary_password: str


class DepartmentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    categories: list[str] = []


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _email_taken(conn, email: str, except_id: str | None = None) -> bool:
    for table, id_col in (("customers", "customer_id"), ("staff", "staff_id"), ("admins", "admin_id")):
        row = conn.execute(
            f"SELECT {id_col} AS id FROM {table} WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
        if row and row["id"] != except_id:
            return True
    return False


def _department_exists(conn, department_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM departments WHERE department_id = ?", (department_id,)
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
@router.get("/api/admin/tickets")
def admin_tickets(admin_id: str = Depends(_admin)):
    conn = db.get_connection()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM tickets ORDER BY created_at DESC")]
    finally:
        conn.close()


@router.get("/api/admin/technicians")
def admin_technicians(admin_id: str = Depends(_admin)):
    """Active technicians with live ticket counts — the reassignment
    dropdown on All Tickets."""
    conn = db.get_connection()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT s.staff_id, s.name, s.email, "
            "COALESCE(SUM(CASE WHEN t.status NOT IN ('resolved', 'closed') THEN 1 ELSE 0 END), 0) "
            "AS active_count, "
            "COALESCE(SUM(CASE WHEN t.status = 'resolved' THEN 1 ELSE 0 END), 0) AS resolved_count "
            "FROM staff s LEFT JOIN tickets t ON t.assigned_technician_id = s.staff_id "
            "WHERE s.role = 'technician' AND s.active = 1 "
            "GROUP BY s.staff_id, s.name, s.email ORDER BY s.name COLLATE NOCASE"
        )]
    finally:
        conn.close()


@router.post("/api/admin/tickets/{ticket_id}/reassign")
def admin_reassign_ticket(ticket_id: str, body: ReassignRequest, admin_id: str = Depends(_admin)):
    """(Re)assign a human-routed ticket to a technician, or return it to
    the unassigned queue (technician_id=null). Logged in assignment
    history and the audit log."""
    conn = db.get_connection()
    try:
        ticket = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        if ticket["route"] != "human":
            raise HTTPException(
                status_code=400, detail="Only tickets routed to a human agent can be (re)assigned"
            )
        if body.technician_id:
            technician = conn.execute(
                "SELECT name FROM staff WHERE staff_id = ? AND role = 'technician' AND active = 1",
                (body.technician_id,),
            ).fetchone()
            if technician is None:
                raise HTTPException(status_code=404, detail="Technician not found")
            note = f"Reassigned to {technician['name']} by the administrator."
        else:
            note = "Returned to the unassigned queue by the administrator."

        if lifecycle.set_assignee(conn, ticket_id, body.technician_id, admin_id):
            insert_ticket_message(conn, ticket_id, "system", note, customer_visible=False)
            if body.technician_id and ticket["status"] == "open":
                lifecycle.set_status(conn, ticket_id, "in_progress", admin_id)
            elif not body.technician_id and ticket["status"] == "in_progress":
                lifecycle.set_status(conn, ticket_id, "open", admin_id)
        conn.commit()
        return {"ticket_id": ticket_id, "assigned_technician_id": body.technician_id}
    finally:
        conn.close()


@router.post("/api/admin/simulate-ticket")
def admin_simulate_ticket(body: SimulateTicketRequest, admin_id: str = Depends(_admin)):
    """Run a ticket through the pipeline without persisting it — the
    per-ticket classify/route trace view."""
    ticket_text = body.ticket_text.strip()
    if not ticket_text:
        raise HTTPException(status_code=400, detail="ticket_text cannot be empty")
    if body.channel == "email":
        ticket_text = clean_email(ticket_text)
    try:
        return runtime.get_graph().invoke({
            "ticket_id": f"SIM-{uuid.uuid4().hex[:8]}",
            "customer_id": body.customer_id or f"SIM-CUST-{uuid.uuid4().hex[:6]}",
            "channel": body.channel,
            "ticket_text": ticket_text,
        })
    except AIError as exc:
        raise HTTPException(status_code=503, detail="The AI provider is unavailable right now") from exc


# ---------------------------------------------------------------------------
# Team (technicians and managers)
# ---------------------------------------------------------------------------
def _staff_rows(conn, staff_id: str | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT s.staff_id, s.name, s.email, s.role, s.active, s.must_change_password, "
        "s.department_id, d.name AS department_name FROM staff s "
        "LEFT JOIN departments d ON d.department_id = s.department_id "
        + ("WHERE s.staff_id = ? " if staff_id else "")
        + "ORDER BY s.role, s.name COLLATE NOCASE",
        (staff_id,) if staff_id else (),
    ).fetchall()
    return [
        {**dict(r), "active": bool(r["active"]), "must_change_password": bool(r["must_change_password"])}
        for r in rows
    ]


def _staff_or_404(conn, staff_id: str):
    row = conn.execute("SELECT * FROM staff WHERE staff_id = ?", (staff_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return row


@router.get("/api/admin/staff")
def admin_list_staff(admin_id: str = Depends(_admin)):
    conn = db.get_connection()
    try:
        return _staff_rows(conn)
    finally:
        conn.close()


@router.post("/api/admin/staff")
def admin_create_staff(body: CreateStaffRequest, admin_id: str = Depends(_admin)):
    """Create a technician or manager (optionally with the manager's
    department) and a temporary password they must change at first
    sign-in."""
    auth.validate_new_password(body.temporary_password)
    email = body.email.strip()
    conn = db.get_connection()
    try:
        if _email_taken(conn, email):
            raise HTTPException(status_code=400, detail="Email already registered")
        if body.department_id and body.role != "manager":
            raise HTTPException(status_code=400, detail="Only managers belong to a department")
        if body.department_id and not _department_exists(conn, body.department_id):
            raise HTTPException(status_code=404, detail="Department not found")
        prefix = "MGR" if body.role == "manager" else "TECH"
        staff_id = f"{prefix}-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO staff (staff_id, name, email, password_hash, role, active, "
            "must_change_password, department_id) VALUES (?, ?, ?, ?, ?, 1, 1, ?)",
            (staff_id, body.name.strip(), email, auth.hash_password(body.temporary_password),
             body.role, body.department_id),
        )
        audit.record(conn, admin_id, "staff.created", "staff", staff_id,
                     role=body.role, department_id=body.department_id)
        conn.commit()
        return _staff_rows(conn, staff_id)[0]
    finally:
        conn.close()


@router.patch("/api/admin/staff/{staff_id}")
def admin_update_staff(staff_id: str, body: UpdateStaffRequest, admin_id: str = Depends(_admin)):
    """Edit name/email, change role (assign or remove the Manager role),
    and set a manager's department. A technician who still owns open
    tickets can't become a manager until they're reassigned. Changing the
    role signs the person out (their token carried the old role)."""
    changes = body.model_dump(exclude_unset=True)
    conn = db.get_connection()
    try:
        person = _staff_or_404(conn, staff_id)
        role = changes.get("role", person["role"])
        updates: dict = {}
        if "name" in changes:
            updates["name"] = changes["name"].strip()
        if "email" in changes:
            email = changes["email"].strip()
            if _email_taken(conn, email, except_id=staff_id):
                raise HTTPException(status_code=400, detail="Email already registered")
            updates["email"] = email
        if role != person["role"]:
            if person["role"] == "technician":
                open_tickets = conn.execute(
                    "SELECT COUNT(*) FROM tickets WHERE assigned_technician_id = ? "
                    "AND status NOT IN ('resolved', 'closed')", (staff_id,)
                ).fetchone()[0]
                if open_tickets:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Reassign their {open_tickets} open ticket(s) before changing the role",
                    )
            updates["role"] = role
        if role == "technician":
            if changes.get("department_id"):
                raise HTTPException(status_code=400, detail="Only managers belong to a department")
            updates["department_id"] = None
        elif "department_id" in changes:
            if changes["department_id"] and not _department_exists(conn, changes["department_id"]):
                raise HTTPException(status_code=404, detail="Department not found")
            updates["department_id"] = changes["department_id"]

        updates = {k: v for k, v in updates.items() if person[k] != v}
        if updates:
            conn.execute(
                f"UPDATE staff SET {', '.join(f'{k} = ?' for k in updates)} WHERE staff_id = ?",
                (*updates.values(), staff_id),
            )
            audit.record(conn, admin_id, "staff.updated", "staff", staff_id, fields=sorted(updates),
                         **{k: updates[k] for k in ("role", "department_id") if k in updates})
            conn.commit()
        return _staff_rows(conn, staff_id)[0]
    finally:
        conn.close()


def _set_staff_active(staff_id: str, active: bool, admin_id: str) -> dict:
    conn = db.get_connection()
    try:
        _staff_or_404(conn, staff_id)
        conn.execute("UPDATE staff SET active = ? WHERE staff_id = ?", (int(active), staff_id))
        audit.record(conn, admin_id, "staff.activated" if active else "staff.deactivated", "staff", staff_id)
        conn.commit()
        return _staff_rows(conn, staff_id)[0]
    finally:
        conn.close()


@router.post("/api/admin/staff/{staff_id}/deactivate")
def admin_deactivate_staff(staff_id: str, admin_id: str = Depends(_admin)):
    """Blocks login and invalidates live sessions immediately. Tickets
    already assigned to them stay put; reassign them from All Tickets."""
    return _set_staff_active(staff_id, False, admin_id)


@router.post("/api/admin/staff/{staff_id}/activate")
def admin_activate_staff(staff_id: str, admin_id: str = Depends(_admin)):
    return _set_staff_active(staff_id, True, admin_id)


@router.post("/api/admin/staff/{staff_id}/reset-password")
def admin_reset_staff_password(staff_id: str, body: ResetPasswordRequest, admin_id: str = Depends(_admin)):
    auth.validate_new_password(body.temporary_password)
    conn = db.get_connection()
    try:
        _staff_or_404(conn, staff_id)
        conn.execute(
            "UPDATE staff SET password_hash = ?, must_change_password = 1 WHERE staff_id = ?",
            (auth.hash_password(body.temporary_password), staff_id),
        )
        audit.record(conn, admin_id, "staff.password_reset", "staff", staff_id)
        conn.commit()
        return _staff_rows(conn, staff_id)[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
def _customer_rows(conn, where: str = "", params: tuple = ()) -> list[dict]:
    rows = conn.execute(
        "SELECT c.customer_id, c.name, c.email, c.active, c.must_change_password, "
        "(SELECT COUNT(*) FROM tickets t WHERE t.customer_id = c.customer_id) AS ticket_count, "
        "(SELECT COUNT(*) FROM chat_sessions s WHERE s.customer_id = c.customer_id) AS chat_count "
        f"FROM customers c {where} ORDER BY c.name COLLATE NOCASE LIMIT 500",
        params,
    ).fetchall()
    return [
        {**dict(r), "active": bool(r["active"]), "must_change_password": bool(r["must_change_password"])}
        for r in rows
    ]


def _customer_or_404(conn, customer_id: str):
    row = conn.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return row


@router.get("/api/admin/customers")
def admin_list_customers(q: str | None = Query(default=None, max_length=200),
                         admin_id: str = Depends(_admin)):
    conn = db.get_connection()
    try:
        if q and q.strip():
            like = f"%{q.strip()}%"
            return _customer_rows(conn, "WHERE c.name LIKE ? OR c.email LIKE ? OR c.customer_id LIKE ?",
                                  (like, like, like))
        return _customer_rows(conn)
    finally:
        conn.close()


@router.post("/api/admin/customers")
def admin_create_customer(body: CreateCustomerRequest, admin_id: str = Depends(_admin)):
    """Create a customer account with a temporary password they must
    change at first sign-in (customers can also sign up themselves)."""
    auth.validate_new_password(body.temporary_password)
    email = body.email.strip()
    conn = db.get_connection()
    try:
        if _email_taken(conn, email):
            raise HTTPException(status_code=400, detail="Email already registered")
        customer_id = f"CUST-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO customers (customer_id, name, email, password_hash, active, must_change_password) "
            "VALUES (?, ?, ?, ?, 1, 1)",
            (customer_id, body.name.strip(), email, auth.hash_password(body.temporary_password)),
        )
        audit.record(conn, admin_id, "customer.created", "customer", customer_id)
        conn.commit()
        return _customer_rows(conn, "WHERE c.customer_id = ?", (customer_id,))[0]
    finally:
        conn.close()


@router.patch("/api/admin/customers/{customer_id}")
def admin_update_customer(customer_id: str, body: UpdateCustomerRequest, admin_id: str = Depends(_admin)):
    changes = body.model_dump(exclude_unset=True)
    conn = db.get_connection()
    try:
        customer = _customer_or_404(conn, customer_id)
        updates = {}
        if "name" in changes:
            updates["name"] = changes["name"].strip()
        if "email" in changes:
            email = changes["email"].strip()
            if _email_taken(conn, email, except_id=customer_id):
                raise HTTPException(status_code=400, detail="Email already registered")
            updates["email"] = email
        updates = {k: v for k, v in updates.items() if customer[k] != v}
        if updates:
            conn.execute(
                f"UPDATE customers SET {', '.join(f'{k} = ?' for k in updates)} WHERE customer_id = ?",
                (*updates.values(), customer_id),
            )
            audit.record(conn, admin_id, "customer.updated", "customer", customer_id, fields=sorted(updates))
            conn.commit()
        return _customer_rows(conn, "WHERE c.customer_id = ?", (customer_id,))[0]
    finally:
        conn.close()


def _set_customer_active(customer_id: str, active: bool, admin_id: str) -> dict:
    conn = db.get_connection()
    try:
        _customer_or_404(conn, customer_id)
        conn.execute("UPDATE customers SET active = ? WHERE customer_id = ?", (int(active), customer_id))
        audit.record(conn, admin_id, "customer.activated" if active else "customer.deactivated",
                     "customer", customer_id)
        conn.commit()
        return _customer_rows(conn, "WHERE c.customer_id = ?", (customer_id,))[0]
    finally:
        conn.close()


@router.post("/api/admin/customers/{customer_id}/deactivate")
def admin_deactivate_customer(customer_id: str, admin_id: str = Depends(_admin)):
    """Blocks sign-in and live sessions. Their tickets and chats are kept."""
    return _set_customer_active(customer_id, False, admin_id)


@router.post("/api/admin/customers/{customer_id}/activate")
def admin_activate_customer(customer_id: str, admin_id: str = Depends(_admin)):
    return _set_customer_active(customer_id, True, admin_id)


@router.post("/api/admin/customers/{customer_id}/reset-password")
def admin_reset_customer_password(customer_id: str, body: ResetPasswordRequest,
                                  admin_id: str = Depends(_admin)):
    auth.validate_new_password(body.temporary_password)
    conn = db.get_connection()
    try:
        _customer_or_404(conn, customer_id)
        conn.execute(
            "UPDATE customers SET password_hash = ?, must_change_password = 1 WHERE customer_id = ?",
            (auth.hash_password(body.temporary_password), customer_id),
        )
        audit.record(conn, admin_id, "customer.password_reset", "customer", customer_id)
        conn.commit()
        return _customer_rows(conn, "WHERE c.customer_id = ?", (customer_id,))[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------
def department_rows(conn, where: str = "", params: tuple = ()) -> list[dict]:
    departments = []
    for row in conn.execute(
        f"SELECT department_id, name, categories FROM departments {where} ORDER BY name COLLATE NOCASE",
        params,
    ):
        dept = dict(row)
        dept["categories"] = json.loads(dept["categories"])
        dept["manager_ids"] = [
            r["staff_id"] for r in conn.execute(
                "SELECT staff_id FROM staff WHERE role = 'manager' AND department_id = ?",
                (dept["department_id"],))
        ]
        departments.append(dept)
    return departments


def _validate_department(conn, body: DepartmentRequest, department_id: str | None) -> list[str]:
    """Categories must be real KB categories, and each category may belong
    to only one department — otherwise "route to the department that owns
    this category" has two answers."""
    categories = sorted(set(body.categories))
    invalid = [c for c in categories if c not in graph.CATEGORIES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown category: {', '.join(invalid)}")
    clash = conn.execute(
        "SELECT 1 FROM departments WHERE name = ? COLLATE NOCASE AND department_id != ?",
        (body.name.strip(), department_id or ""),
    ).fetchone()
    if clash:
        raise HTTPException(status_code=400, detail="A department with that name already exists")
    for other in department_rows(conn):
        if other["department_id"] == department_id:
            continue
        overlap = sorted(set(categories) & set(other["categories"]))
        if overlap:
            raise HTTPException(
                status_code=400, detail=f"{', '.join(overlap)} already belongs to {other['name']}"
            )
    return categories


@router.get("/api/admin/departments")
def admin_list_departments(admin_id: str = Depends(_admin)):
    conn = db.get_connection()
    try:
        return {"departments": department_rows(conn), "categories": graph.CATEGORIES}
    finally:
        conn.close()


@router.post("/api/admin/departments")
def admin_create_department(body: DepartmentRequest, admin_id: str = Depends(_admin)):
    conn = db.get_connection()
    try:
        categories = _validate_department(conn, body, None)
        department_id = f"DEPT-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO departments (department_id, name, categories) VALUES (?, ?, ?)",
            (department_id, body.name.strip(), json.dumps(categories)),
        )
        audit.record(conn, admin_id, "department.created", "department", department_id,
                     name=body.name.strip(), categories=categories)
        conn.commit()
        return department_rows(conn, "WHERE department_id = ?", (department_id,))[0]
    finally:
        conn.close()


@router.put("/api/admin/departments/{department_id}")
def admin_update_department(department_id: str, body: DepartmentRequest, admin_id: str = Depends(_admin)):
    conn = db.get_connection()
    try:
        if not _department_exists(conn, department_id):
            raise HTTPException(status_code=404, detail="Department not found")
        categories = _validate_department(conn, body, department_id)
        conn.execute(
            "UPDATE departments SET name = ?, categories = ? WHERE department_id = ?",
            (body.name.strip(), json.dumps(categories), department_id),
        )
        audit.record(conn, admin_id, "department.updated", "department", department_id,
                     name=body.name.strip(), categories=categories)
        conn.commit()
        return department_rows(conn, "WHERE department_id = ?", (department_id,))[0]
    finally:
        conn.close()


@router.delete("/api/admin/departments/{department_id}")
def admin_delete_department(department_id: str, admin_id: str = Depends(_admin)):
    """Its managers are left without a department (staff.department_id is
    cleared by the foreign key) until the admin assigns another."""
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT name FROM departments WHERE department_id = ?", (department_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Department not found")
        conn.execute("DELETE FROM departments WHERE department_id = ?", (department_id,))
        audit.record(conn, admin_id, "department.deleted", "department", department_id, name=row["name"])
        conn.commit()
        return {"department_id": department_id, "deleted": True}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _settings_payload() -> dict:
    return {
        "sections": {name: app_settings.get(name).model_dump() for name in app_settings.SECTIONS},
        "defaults": {name: app_settings.defaults(name).model_dump() for name in app_settings.SECTIONS},
        "categories": graph.CATEGORIES,
        "language_tags": list(app_settings.LANGUAGE_TAGS),
    }


@router.get("/api/admin/settings")
def admin_get_settings(admin_id: str = Depends(_admin)):
    return _settings_payload()


@router.put("/api/admin/settings/{section}")
def admin_save_settings(
    section: Literal["routing", "incidents", "statuses"], values: dict, admin_id: str = Depends(_admin)
):
    """Save a whole section (validated). Applies to the next message,
    correlation run or page load."""
    conn = db.get_connection()
    try:
        before = app_settings.get(section).model_dump()
        try:
            saved = app_settings.save(conn, section, values, admin_id)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail="; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors()),
            ) from exc
        changed = sorted(k for k, v in saved.model_dump().items() if before.get(k) != v)
        audit.record(conn, admin_id, "settings.updated", "settings", section, fields=changed)
        conn.commit()
    finally:
        conn.close()
    return _settings_payload()


@router.delete("/api/admin/settings/{section}")
def admin_reset_settings(section: Literal["routing", "incidents", "statuses"], admin_id: str = Depends(_admin)):
    """Back to the environment/default values."""
    conn = db.get_connection()
    try:
        app_settings.reset(conn, section)
        audit.record(conn, admin_id, "settings.reset", "settings", section)
        conn.commit()
    finally:
        conn.close()
    return _settings_payload()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
@router.get("/api/admin/audit")
def admin_audit_log(
    entity: str | None = Query(default=None, max_length=40),
    entity_id: str | None = Query(default=None, max_length=80),
    action: str | None = Query(default=None, max_length=80),
    actor_id: str | None = Query(default=None, max_length=80),
    since: str | None = Query(default=None, max_length=40),
    until: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    admin_id: str = Depends(_admin),
):
    """Newest first. `action` matches a prefix (e.g. "ticket." or
    "auth.login_failed"); since/until are ISO timestamps."""
    clauses, params = [], []
    for column, value in (("entity", entity), ("entity_id", entity_id), ("actor_id", actor_id)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if action:
        clauses.append("action LIKE ?")
        params.append(f"{action}%")
    if since:
        clauses.append("at >= ?")
        params.append(since)
    if until:
        clauses.append("at <= ?")
        params.append(until)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = db.get_connection()
    try:
        names = {audit.ANONYMOUS: "Anonymous", lifecycle.SYSTEM: "System (AI pipeline)"}
        for table, id_col in (("staff", "staff_id"), ("admins", "admin_id"), ("customers", "customer_id")):
            for row in conn.execute(f"SELECT {id_col} AS id, name FROM {table}"):
                names[row["id"]] = row["name"]
        total = conn.execute(f"SELECT COUNT(*) FROM audit_events {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM audit_events {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return {
            "total": total,
            "events": [
                {**dict(r), "detail": json.loads(r["detail"]), "actor_name": names.get(r["actor_id"], r["actor_id"])}
                for r in rows
            ],
        }
    finally:
        conn.close()
