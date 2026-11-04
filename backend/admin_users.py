"""User management for the admin: one list of customers, technicians and
managers (admin role only, enforced on every endpoint).

  GET    /api/admin/users                       ?role= &q= &status=active|inactive
  POST   /api/admin/users                       create (role customer/technician/manager)
  PATCH  /api/admin/users/{user_id}             name, email, staff role, manager department
  POST   /api/admin/users/{user_id}/deactivate  blocks sign-in and live sessions
  POST   /api/admin/users/{user_id}/activate
  POST   /api/admin/users/{user_id}/reset-password

Admin accounts can't be created or changed here: the one admin is seeded
from ADMIN_EMAIL/ADMIN_PASSWORD (auth.ensure_admin). A new account and a
reset get a generated temporary password, returned once in that response
(never stored in clear, never shown again) and changed at first sign-in.
A deactivated user can't sign in; their tickets and chats stay. Every
change is in the audit log (who, what, when; never a password).

Customers live in `customers`, technicians and managers in `staff`; a
customer can't become staff (or the other way) by an edit. The work is
done by the same functions as the older /api/admin/staff and
/api/admin/customers endpoints (backend/admin.py).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from backend import admin, auth, db

router = APIRouter()

_admin = auth.get_current_admin

Role = Literal["customer", "technician", "manager"]


class CreateUserRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    role: str
    department_id: str | None = None


class UpdateUserRequest(BaseModel):
    """Only the fields present are changed."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, min_length=3, max_length=254)
    role: str | None = None
    department_id: str | None = None


def _user(row: dict, role: str) -> dict:
    """One shape for every role."""
    return {
        "user_id": row.get("customer_id") or row.get("staff_id"),
        "role": role,
        "name": row["name"],
        "email": row["email"],
        "active": row["active"],
        "must_change_password": row["must_change_password"],
        "department_id": row.get("department_id"),
        "department_name": row.get("department_name"),
        "ticket_count": row.get("ticket_count"),
        **({"temporary_password": row["temporary_password"]} if "temporary_password" in row else {}),
    }


def _kind(conn, user_id: str) -> Literal["customer", "staff"]:
    """Which table the user is in. The admin account is never managed here."""
    if conn.execute("SELECT 1 FROM customers WHERE customer_id = ?", (user_id,)).fetchone():
        return "customer"
    if conn.execute("SELECT 1 FROM staff WHERE staff_id = ?", (user_id,)).fetchone():
        return "staff"
    if conn.execute("SELECT 1 FROM admins WHERE admin_id = ?", (user_id,)).fetchone():
        raise HTTPException(status_code=403, detail="The admin account is managed through the environment")
    raise HTTPException(status_code=404, detail="User not found")


def _kind_of(user_id: str) -> Literal["customer", "staff"]:
    conn = db.get_connection()
    try:
        return _kind(conn, user_id)
    finally:
        conn.close()


def _check_role(role: str) -> Role:
    if role == "admin":
        raise HTTPException(status_code=400, detail="Admin accounts can't be created here")
    if role not in ("customer", "technician", "manager"):
        raise HTTPException(status_code=400, detail="Role must be customer, technician or manager")
    return role  # type: ignore[return-value]


def _staff_ticket_counts(conn) -> dict[str, int]:
    return {r["id"]: r["n"] for r in conn.execute(
        "SELECT assigned_technician_id AS id, COUNT(*) AS n FROM tickets "
        "WHERE assigned_technician_id IS NOT NULL GROUP BY assigned_technician_id")}


@router.get("/api/admin/users")
def list_users(
    role: Role | None = None,
    q: str | None = Query(default=None, max_length=200),
    status: Literal["active", "inactive"] | None = None,
    admin_id: str = Depends(_admin),
):
    """Customers, technicians and managers, by name. q matches name, email
    or id; ticket_count is a customer's tickets or a technician's
    assigned tickets."""
    conn = db.get_connection()
    try:
        users = []
        if role in (None, "customer"):
            users += [_user(r, "customer") for r in admin._customer_rows(conn)]
        if role != "customer":
            counts = _staff_ticket_counts(conn)
            users += [_user({**r, "ticket_count": counts.get(r["staff_id"], 0)}, r["role"])
                      for r in admin._staff_rows(conn) if role in (None, r["role"])]
    finally:
        conn.close()
    if q and q.strip():
        needle = q.strip().lower()
        users = [u for u in users if needle in u["name"].lower() or needle in u["email"].lower()
                 or needle in u["user_id"].lower()]
    if status:
        users = [u for u in users if u["active"] is (status == "active")]
    return sorted(users, key=lambda u: (u["name"].lower(), u["user_id"]))


@router.post("/api/admin/users")
def create_user(body: CreateUserRequest, response: Response, admin_id: str = Depends(_admin)):
    """The new account with its temporary password (this response only)."""
    role = _check_role(body.role)
    if role == "customer":
        if body.department_id:
            raise HTTPException(status_code=400, detail="Only managers belong to a department")
        row = admin.create_customer(admin.CreateCustomerRequest(name=body.name, email=body.email),
                                    admin_id, response)
    else:
        row = admin.create_staff(admin.CreateStaffRequest(name=body.name, email=body.email, role=role,
                                                          department_id=body.department_id),
                                 admin_id, response)
    return _user(row, role)


@router.patch("/api/admin/users/{user_id}")
def update_user(user_id: str, body: UpdateUserRequest, admin_id: str = Depends(_admin)):
    changes = body.model_dump(exclude_unset=True)
    if "role" in changes:
        _check_role(changes["role"])
    if _kind_of(user_id) == "customer":
        if changes.get("role") not in (None, "customer"):
            raise HTTPException(status_code=400, detail="A customer account can't become staff; create a staff account")
        if changes.get("department_id"):
            raise HTTPException(status_code=400, detail="Only managers belong to a department")
        row = admin.update_customer(user_id, admin.UpdateCustomerRequest(
            **{k: v for k, v in changes.items() if k in ("name", "email")}), admin_id)
        return _user(row, "customer")
    if changes.get("role") == "customer":
        raise HTTPException(status_code=400, detail="A staff account can't become a customer")
    row = admin.update_staff(user_id, admin.UpdateStaffRequest(**changes), admin_id)
    return _user(row, row["role"])


def _set_active(user_id: str, active: bool, admin_id: str) -> dict:
    if _kind_of(user_id) == "customer":
        return _user(admin.set_customer_active(user_id, active, admin_id), "customer")
    row = admin.set_staff_active(user_id, active, admin_id)
    return _user(row, row["role"])


@router.post("/api/admin/users/{user_id}/deactivate")
def deactivate_user(user_id: str, admin_id: str = Depends(_admin)):
    """They can't sign in, and live sessions stop working at once. Their
    tickets and chats stay; reassign a technician's open tickets."""
    return _set_active(user_id, False, admin_id)


@router.post("/api/admin/users/{user_id}/activate")
def activate_user(user_id: str, admin_id: str = Depends(_admin)):
    return _set_active(user_id, True, admin_id)


@router.post("/api/admin/users/{user_id}/reset-password")
def reset_user_password(user_id: str, response: Response, admin_id: str = Depends(_admin)):
    """A new temporary password (this response only); the old one stops
    working and the user must change it at next sign-in."""
    if _kind_of(user_id) == "customer":
        return _user(admin.reset_customer_password(user_id, admin_id, response), "customer")
    row = admin.reset_staff_password(user_id, admin_id, response)
    return _user(row, row["role"])
