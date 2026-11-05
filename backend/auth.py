"""Login, customer signup, admin bootstrap, and JWT session handling.

Roles: customer, technician, manager, admin. Customers sign up publicly.
Technicians and managers (the `staff` table) are created only by the
admin. The admin is a single account bootstrapped from env on startup
(`ensure_admin`) — there is no admin signup endpoint at all.

One login endpoint serves every role: it looks the email up across the
three identity tables and the token carries the role, which the frontend
uses to pick a portal. Emails are kept unique across tables (checked on
every account creation) so that lookup is never ambiguous.

Every dependency re-reads the user's row on each request rather than
trusting the token alone, so a deactivated account (staff or customer) or
a pending forced password change takes effect immediately, not when the
24h token expires. Failed sign-ins are recorded in the audit log.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend import audit, db, rate_limit
from backend.repositories import accounts

logger = logging.getLogger(__name__)

_DEV_JWT_SECRET = "dev-secret-change-me"
JWT_SECRET = os.environ.get("JWT_SECRET", _DEV_JWT_SECRET)
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

Role = Literal["customer", "technician", "manager", "admin"]
STAFF_ROLES = ("technician", "manager")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72  # bcrypt's limit

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
    # bcrypt reads at most 72 bytes (bcrypt 5 raises on more; earlier
    # versions cut silently, so a legacy hash still matches the cut form).
    return bcrypt.checkpw(password.encode()[:MAX_PASSWORD_BYTES], password_hash.encode())


def validate_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at most {MAX_PASSWORD_BYTES} bytes",
        )


def generate_temporary_password() -> str:
    """A random temporary password for an admin-created account or a
    reset: returned to the admin once, stored only as a bcrypt hash, and
    the account must change it at first sign-in."""
    return secrets.token_urlsafe(12)  # 16 characters, ~96 bits


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


email_in_use = accounts.email_in_use


# ---------------------------------------------------------------------------
# Admin bootstrap
# ---------------------------------------------------------------------------
def config_error(variable: str, problem: str) -> RuntimeError:
    """One plain line on stderr naming the variable (never its value), so
    the AppSail log shows exactly what to fix, then the exception that
    stops startup."""
    message = f"STARTUP CONFIG ERROR: {variable} {problem}; refusing to start."
    print(message, file=sys.stderr, flush=True)
    return RuntimeError(message)


def on_appsail() -> bool:
    """AppSail sets X_ZOHO_CATALYST_LISTEN_PORT for every service."""
    return bool(os.environ.get("X_ZOHO_CATALYST_LISTEN_PORT"))


# Words that mark a value copied from an example rather than generated.
_PLACEHOLDER_WORDS = ("change", "example", "replace", "placeholder", "default", "secret", "password")
# Passwords that stay weak however they are padded with digits or symbols.
_COMMON_PASSWORDS = frozenset({"password", "admin", "administrator", "qwerty", "letmein", "welcome",
                               "changeme", "secret", "ticketapp", "iloveyou", "abc"})
MIN_ADMIN_PASSWORD_LENGTH = 12


def jwt_secret_problem(secret: str) -> str | None:
    """Why `secret` is unfit to sign tokens in production, or None."""
    if not secret.strip():
        return "is missing"
    if secret == _DEV_JWT_SECRET:
        return "is the public development default"
    if len(secret) < 32:
        return "is too short (needs at least 32 characters)"
    lowered = secret.lower()
    if len(set(secret)) < 10 or any(word in lowered for word in _PLACEHOLDER_WORDS):
        return "looks like a placeholder, not a random value (use e.g. `openssl rand -hex 32`)"
    return None


def admin_password_problem(password: str, email: str = "") -> str | None:
    """Why the bootstrap admin password is too weak for production, or None."""
    if password == _DEV_ADMIN_PASSWORD:
        return "is the public development default"
    if len(password) < MIN_ADMIN_PASSWORD_LENGTH:
        return f"is too short (needs at least {MIN_ADMIN_PASSWORD_LENGTH} characters)"
    if len(set(password)) < 6:
        return "repeats too few characters"
    letters = "".join(ch for ch in password.lower() if ch.isalpha())
    local = email.split("@", 1)[0].lower()
    if letters in _COMMON_PASSWORDS or (len(local) >= 4 and local in password.lower()):
        return "is a common password or contains the admin email"
    return None


def check_production_config() -> None:
    """Fail closed before anything else starts:
    - on AppSail, APP_ENV must be set explicitly (unset would mean
      development: the dev admin, with a password in the source, on a
      public URL);
    - in production, JWT_SECRET must be set, not the public dev default,
      at least 32 characters and not a placeholder (else anyone could
      forge a token); ADMIN_PASSWORD, when set, must not be weak; with
      DB_BACKEND=postgres the database password must not be weak and SSL
      must be required (db_postgres.production_problems).
    Errors name the variable, never its value."""
    if on_appsail() and not os.environ.get("APP_ENV", "").strip():
        raise config_error("APP_ENV", "is missing (set APP_ENV=production in the AppSail console)")
    if not is_production():
        return
    problem = jwt_secret_problem(JWT_SECRET if os.environ.get("JWT_SECRET", "").strip() else "")
    if problem:
        raise config_error("JWT_SECRET", problem)
    password = os.environ.get("ADMIN_PASSWORD", "")
    if password:
        problem = admin_password_problem(password, os.environ.get("ADMIN_EMAIL", "").strip())
        if problem:
            raise config_error("ADMIN_PASSWORD", problem)
    if db.is_postgres():
        from backend import db_postgres

        for variable, problem in db_postgres.production_problems():
            raise config_error(variable, problem)


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
                missing = " and ".join(n for n, v in (("ADMIN_EMAIL", email), ("ADMIN_PASSWORD", password)) if not v)
                raise config_error(missing, "is missing (needed to create the first admin)")
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
        candidates = accounts.login_candidates(conn, email)
        for row, role, must_change, active in candidates:
            if verify_password(password, row["password_hash"]):
                if not active:
                    _login_failed(conn, email, "deactivated", row["id"])
                    raise HTTPException(status_code=403, detail="This account has been deactivated")
                return {
                    "user_id": row["id"],
                    "role": role,
                    "name": row["name"],
                    "must_change_password": must_change,
                    "token": create_token(row["id"], role),
                }
        _login_failed(conn, email, "bad_credentials", candidates[0][0]["id"] if candidates else None)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    finally:
        conn.close()


def _login_failed(conn, email: str, reason: str, account_id: str | None) -> None:
    """Audit a failed sign-in: the attempted email (truncated), why, and
    the matching account if any. Never the password."""
    audit.record(conn, audit.ANONYMOUS, "auth.login_failed", "account", account_id,
                 email=email[:254], reason=reason)
    conn.commit()


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
        if table in ("staff", "customers"):
            conn.execute(
                f"UPDATE {table} SET password_hash = ?, must_change_password = 0 WHERE {id_col} = ?",
                (hash_password(new_password), user["id"]),
            )
        else:
            conn.execute(
                f"UPDATE {table} SET password_hash = ? WHERE {id_col} = ?",
                (hash_password(new_password), user["id"]),
            )
        audit.record(conn, user["id"], "auth.password_changed", "account", user["id"])
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
    # A role change by the admin invalidates tokens minted for the old role.
    if table == "staff" and row["role"] != role:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if not row.get("active", 1):
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


def get_ai_customer(request: Request, customer_id: str = Depends(get_current_customer)) -> str:
    """get_current_customer for an endpoint that runs the AI: the call
    also counts as one AI turn for this customer and their IP, and is
    refused with 429 over the limit (rate_limit.check_ai_turn)."""
    rate_limit.check_ai_turn(customer_id, rate_limit.client_ip(request))
    return customer_id
