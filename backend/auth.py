"""Customer + agent + admin signup/login and JWT session handling.

Each role is kept as its own table with its own endpoints (no shared
`users` + role flag) so the later Azure AD B2C migration only has to
deal with clean, independent identity flows, not an artificial merge.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend import db

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

_bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(subject_id: str, role: Literal["customer", "agent", "admin"]) -> str:
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


def signup_customer(name: str, email: str, password: str) -> dict:
    conn = db.get_connection()
    try:
        existing = conn.execute(
            "SELECT 1 FROM customers WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        customer_id = f"CUST-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO customers (customer_id, name, email, password_hash) "
            "VALUES (?, ?, ?, ?)",
            (customer_id, name, email, hash_password(password)),
        )
        conn.commit()
        return {"customer_id": customer_id, "token": create_token(customer_id, "customer")}
    finally:
        conn.close()


def login_customer(email: str, password: str) -> dict:
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT customer_id, password_hash FROM customers WHERE email = ?",
            (email,),
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        return {
            "customer_id": row["customer_id"],
            "token": create_token(row["customer_id"], "customer"),
        }
    finally:
        conn.close()


def signup_agent(name: str, email: str, password: str) -> dict:
    conn = db.get_connection()
    try:
        existing = conn.execute(
            "SELECT 1 FROM agents WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        agent_id = f"AGENT-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO agents (agent_id, name, email, password_hash) "
            "VALUES (?, ?, ?, ?)",
            (agent_id, name, email, hash_password(password)),
        )
        conn.commit()
        return {"agent_id": agent_id, "token": create_token(agent_id, "agent")}
    finally:
        conn.close()


def login_agent(email: str, password: str) -> dict:
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT agent_id, password_hash FROM agents WHERE email = ?",
            (email,),
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        return {
            "agent_id": row["agent_id"],
            "token": create_token(row["agent_id"], "agent"),
        }
    finally:
        conn.close()


def signup_admin(name: str, email: str, password: str) -> dict:
    conn = db.get_connection()
    try:
        existing = conn.execute(
            "SELECT 1 FROM admins WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        admin_id = f"ADMIN-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO admins (admin_id, name, email, password_hash) "
            "VALUES (?, ?, ?, ?)",
            (admin_id, name, email, hash_password(password)),
        )
        conn.commit()
        return {"admin_id": admin_id, "token": create_token(admin_id, "admin")}
    finally:
        conn.close()


def login_admin(email: str, password: str) -> dict:
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT admin_id, password_hash FROM admins WHERE email = ?",
            (email,),
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        return {
            "admin_id": row["admin_id"],
            "token": create_token(row["admin_id"], "admin"),
        }
    finally:
        conn.close()


def get_current_customer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency: returns the authenticated customer_id."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    payload = _decode_token(credentials.credentials)
    if payload.get("role") != "customer":
        raise HTTPException(status_code=403, detail="Customer token required")
    return payload["sub"]


def get_current_agent(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency: returns the authenticated agent_id."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    payload = _decode_token(credentials.credentials)
    if payload.get("role") != "agent":
        raise HTTPException(status_code=403, detail="Agent token required")
    return payload["sub"]


def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency: returns the authenticated admin_id."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    payload = _decode_token(credentials.credentials)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin token required")
    return payload["sub"]
