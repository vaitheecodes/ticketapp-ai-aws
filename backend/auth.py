"""Login, customer signup, admin bootstrap, and JWT session handling.

Roles: customer, technician, manager, admin. Customers sign up publicly.
Technicians and managers (the `staff` table) are created only by the
admin. The admin is a single account bootstrapped from env on startup
(`ensure_admin`) — there is no admin signup endpoint at all.

One login endpoint serves every role: it looks the email up across the
three identity tables and the token carries the role, which the frontend
uses to pick a portal. Emails are kept unique across tables (checked on
every account creation) so that lookup is never ambiguous.

Staff and admin dependencies re-read their row on every request rather
than trusting the token alone, so a deactivated account or a pending
forced password change takes effect immediately, not when the 24h token
expires.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend import db

logger = logging.getLogger(__name__)

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

Role = Literal["customer", "technician", "manager", "admin"]
STAFF_ROLES = ("technician", "manager")
MIN_PASSWORD_LENGTH = 8

# Used only outside production when ADMIN_EMAIL/ADMIN_PASSWORD are unset,
# so a fresh checkout still has a usable admin. ensure_admin() refuses to
# fall back to these when APP_ENV=production.
_DEV_ADMIN_EMAIL = "admin@example.com"
_DEV_ADMIN_PASSWORD = "admin-dev-password"

_bearer_scheme = HTTPBearer(auto_error=False)


def is_production() -> bool:
    return os.environ.get("APP_ENV", "development").strip().lower() == "production"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )


def create_token(subject_id: str, role: Role) -> str:
    payload = {
        "sub": subject_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


def email_in_use(conn, email: str) -> bool:
    return any(
        conn.execute(f"SELECT 1 FROM {table} WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
        for table in ("customers", "staff", "admins")
    )


# ---------------------------------------------------------------------------
# Admin bootstrap
# ---------------------------------------------------------------------------
def ensure_admin() -> None:
    """Create the single admin from ADMIN_EMAIL/ADMIN_PASSWORD if no admin
    exists yet. Never overwrites an existing admin (so rotating the env
    var does not silently reset a password someone changed). Outside
    production, missing vars fall back to a dev admin with a loud
    warning; in production they are required and startup fails."""
    email = os.environ.get("ADMIN_EMAIL", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")

    conn = db.get_connection()
    try:
        admin_count = conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
        if admin_count:
            if admin_count > 1:
                logger.warning(
                    "%d admin accounts exist (left over from the old public admin "
                    "signup). Only one is expected; remove the extras manually.",
                    admin_count,
                )
            return

        if not email or not password:
            if is_production():
                raise RuntimeError(
                    "APP_ENV=production but ADMIN_EMAIL/ADMIN_PASSWORD are not set; "
                    "refusing to start without an admin account."
                )
            email, password = _DEV_ADMIN_EMAIL, _DEV_ADMIN_PASSWORD
            logger.warning(
                "\n%s\nADMIN_EMAIL/ADMIN_PASSWORD not set: created a DEV-ONLY admin "
                "(%s, password in backend/auth.py). Set both env vars before any "
                "real deployment.\n%s",
                "!" * 72, email, "!" * 72,
            )

        if email_in_use(conn, email):
            raise RuntimeError(
                f"ADMIN_EMAIL {email} is already used by a customer or staff account."
            )
        conn.execute(
            "INSERT INTO admins (admin_id, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (f"ADMIN-{uuid.uuid4().hex[:8]}", "Administrator", email, hash_password(password)),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Signup / login / password change
# ---------------------------------------------------------------------------
def signup_customer(name: str, email: str, password: str) -> dict:
    validate_new_password(password)
    conn = db.get_connection()
    try:
        if email_in_use(conn, email):
            raise HTTPException(status_code=400, detail="Email already registered")

        customer_id = f"CUST-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO customers (customer_id, name, email, password_hash) "
            "VALUES (?, ?, ?, ?)",
            (customer_id, name, email, hash_password(password)),
        )
        conn.commit()
        return {
            "user_id": customer_id,
            "role": "customer",
            "name": name,
            "must_change_password": False,
            "token": create_token(customer_id, "customer"),
        }
    finally:
        conn.close()


def login(email: str, password: str) -> dict:
    """Single login for every role. Checks admin, then staff, then
    customers; emails are unique across tables for new accounts, and the
    order only matters for legacy duplicates created before that rule."""
    conn = db.get_connection()
    try:
        candidates = []
        row = conn.execute(
            "SELECT admin_id AS id, name, password_hash FROM admins "
            "WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone()
        if row:
            candidates.append((row, "admin", False, True))
        row = conn.execute(
            "SELECT staff_id AS id, name, password_hash, role, active, must_change_password "
            "FROM staff WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone()
        if row:
            candidates.append((row, row["role"], bool(row["must_change_password"]), bool(row["active"])))
        row = conn.execute(
            "SELECT customer_id AS id, name, password_hash FROM customers "
            "WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone()
        if row:
            candidates.append((row, "customer", False, True))

        for row, role, must_change, active in candidates:
            if verify_password(password, row["password_hash"]):
                if not active:
                    raise HTTPException(status_code=403, detail="This account has been deactivated")
                return {
                    "user_id": row["id"],
                    "role": role,
                    "name": row["name"],
                    "must_change_password": must_change,
                    "token": create_token(row["id"], role),
                }
        raise HTTPException(status_code=401, detail="Invalid email or password")
    finally:
        conn.close()


def change_password(user: dict, current_password: str, new_password: str) -> None:
    validate_new_password(new_password)
    if current_password == new_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one")
    table, id_col = _IDENTITY_TABLES[user["role"]]
    conn = db.get_connection()
    try:
        row = conn.execute(
            f"SELECT password_hash FROM {table} WHERE {id_col} = ?", (user["id"],)
        ).fetchone()
        if row is None or not verify_password(current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        if table == "staff":
            conn.execute(
                "UPDATE staff SET password_hash = ?, must_change_password = 0 WHERE staff_id = ?",
                (hash_password(new_password), user["id"]),
            )
        else:
            conn.execute(
                f"UPDATE {table} SET password_hash = ? WHERE {id_col} = ?",
                (hash_password(new_password), user["id"]),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------
_IDENTITY_TABLES = {
    "customer": ("customers", "customer_id"),
    "technician": ("staff", "staff_id"),
    "manager": ("staff", "staff_id"),
    "admin": ("admins", "admin_id"),
}


def _load_user(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    payload = _decode_token(credentials.credentials)
    role = payload.get("role")
    if role not in _IDENTITY_TABLES:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    table, id_col = _IDENTITY_TABLES[role]

    conn = db.get_connection()
    try:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE {id_col} = ?", (payload["sub"],)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    row = dict(row)
    if table == "staff":
        # A role change by the admin invalidates tokens minted for the old role.
        if row["role"] != role:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        if not row["active"]:
            raise HTTPException(status_code=403, detail="This account has been deactivated")
    return {
        "id": payload["sub"],
        "role": role,
        "name": row["name"],
        "email": row["email"],
        "must_change_password": bool(row.get("must_change_password", 0)),
    }


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict:
    """Any authenticated, active user — including one who still has to
    change their temporary password (so /me and /change-password work)."""
    return _load_user(credentials)


def require_user(*roles: Role):
    """Dependency factory: the authenticated user must have one of
    `roles` and must not have a pending forced password change. Returns
    the user dict (id, role, name, email) — for endpoints whose behaviour
    depends on which role is calling."""

    def dependency(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    ) -> dict:
        user = _load_user(credentials)
        if user["role"] not in roles:
            raise HTTPException(
                status_code=403, detail=f"{' or '.join(r.capitalize() for r in roles)} role required"
            )
        if user["must_change_password"]:
            raise HTTPException(status_code=403, detail="Password change required")
        return user

    return dependency


def require_role(*roles: Role):
    """Like require_user, but returns only the user's id."""
    check = require_user(*roles)

    def dependency(user: dict = Depends(check)) -> str:
        return user["id"]

    return dependency


get_current_customer = require_role("customer")
get_current_technician = require_role("technician")
get_current_manager = require_role("manager")
get_current_admin = require_role("admin")
