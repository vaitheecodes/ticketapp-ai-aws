"""Account lookup by email: case-insensitive on every backend (lower()
on both sides; Postgres also has unique indexes on lower(email))."""

from __future__ import annotations

ACCOUNT_TABLES = ("customers", "staff", "admins")


def email_in_use(conn, email: str) -> bool:
    """Emails are unique across all three account tables."""
    return any(
        conn.execute(f"SELECT 1 FROM {table} WHERE lower(email) = lower(?)", (email,)).fetchone()
        for table in ACCOUNT_TABLES
    )


def login_candidates(conn, email: str) -> list[tuple]:
    """(row, role, must_change_password, active) for every account with
    this email: admin, then staff, then customer."""
    candidates = []
    row = conn.execute(
        "SELECT admin_id AS id, name, password_hash FROM admins WHERE lower(email) = lower(?)",
        (email,),
    ).fetchone()
    if row:
        candidates.append((row, "admin", False, True))
    row = conn.execute(
        "SELECT staff_id AS id, name, password_hash, role, active, must_change_password "
        "FROM staff WHERE lower(email) = lower(?)",
        (email,),
    ).fetchone()
    if row:
        candidates.append((row, row["role"], bool(row["must_change_password"]), bool(row["active"])))
    row = conn.execute(
        "SELECT customer_id AS id, name, password_hash, active, must_change_password "
        "FROM customers WHERE lower(email) = lower(?)",
        (email,),
    ).fetchone()
    if row:
        candidates.append((row, "customer", bool(row["must_change_password"]), bool(row["active"])))
    return candidates
