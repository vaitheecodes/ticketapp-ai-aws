"""Seed data, all idempotent so repeated `init_db()` calls (every app
startup) never duplicate or reset data a tester may have relied on.

- Mock `orders` — so generate_response's order-lookup grounding
  (backend/graph.py:lookup_order) has real records to answer from,
  instead of inventing plausible-sounding order details. Faker-driven,
  fixed seed, same convention as src/pipeline/02_silver_transform.py
  (SEED = 42) for reproducibility. Skipped if `orders` already has rows.
- Default `departments` — skipped if any department exists, so an
  admin's edits survive restarts.
- One demo manager + one demo technician — dev only (APP_ENV !=
  production), skipped per email if it already exists.
"""

from __future__ import annotations

import json
import os
import random
import uuid
from datetime import date, timedelta

import bcrypt
from faker import Faker

SEED = 42
NUM_ORDERS = 80

_STATUSES = ["processing", "shipped", "delivered", "cancelled"]
_STATUS_WEIGHTS = [0.25, 0.25, 0.40, 0.10]

_ITEM_CATALOG = [
    "Wireless Headphones", "USB-C Cable", "Laptop Stand", "Mechanical Keyboard",
    "Desk Lamp", "Water Bottle", "Yoga Mat", "Bluetooth Speaker", "Phone Case",
    "Backpack", "Running Shoes", "Coffee Mug", "Notebook Set", "Webcam",
    "Wireless Mouse", "Monitor Arm", "Office Chair", "Standing Desk",
]

# A few fixed, memorable emails (not just random Faker output) so a
# tester can sign up as one of these and immediately have real order
# matches to test against, without hunting through seeded data first.
DEMO_EMAILS = ["demo@example.com", "customer@example.com", "test@example.com"]


def seed_orders(conn) -> None:
    already_seeded = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    if already_seeded:
        return

    fake = Faker()
    Faker.seed(SEED)
    rng = random.Random(SEED)

    rows = []
    for i in range(NUM_ORDERS):
        order_id = f"ORD-{100000 + i}"
        # first few orders per demo email so they're easy to find/test with
        email = DEMO_EMAILS[i % len(DEMO_EMAILS)] if i < len(DEMO_EMAILS) * 4 else fake.email()
        status = rng.choices(_STATUSES, weights=_STATUS_WEIGHTS)[0]
        items = ", ".join(rng.sample(_ITEM_CATALOG, k=rng.randint(1, 3)))
        order_date = (date.today() - timedelta(days=rng.randint(1, 60))).isoformat()
        rows.append((order_id, email, status, items, order_date))

    conn.executemany(
        "INSERT INTO orders (order_id, customer_email, status, items, order_date) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


# Every one of the 17 KB categories (graph.CATEGORIES) is owned by exactly
# one default department, so no category starts out unroutable.
DEFAULT_DEPARTMENTS = [
    ("Monitoring & Alerts", ["MONITORING", "WEB_MONITORING", "ALERTS"]),
    ("Infrastructure", ["SERVER_MONITORING", "AGENT", "CONTAINERS", "CLOUD"]),
    ("Network", ["SNMP", "NETFLOW"]),
    ("Applications & Logs", ["APPLICATION_MONITORING", "APM", "LOGS"]),
    ("Integrations", ["INTEGRATION"]),
    ("Account & Data", ["ACCOUNT", "DATA"]),
    ("Customer Success", ["REPORTING", "SUPPORT"]),
]


def seed_departments(conn) -> None:
    if conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]:
        return
    conn.executemany(
        "INSERT INTO departments (department_id, name, categories) VALUES (?, ?, ?)",
        [
            (f"DEPT-{uuid.uuid4().hex[:8]}", name, json.dumps(categories))
            for name, categories in DEFAULT_DEPARTMENTS
        ],
    )
    conn.commit()


# Dev-only accounts so every portal can be tried without the admin first
# creating staff. Never seeded when APP_ENV=production.
DEMO_STAFF = [
    {
        "name": "Demo Manager",
        "email": "manager@example.com",
        "password": "manager-demo-pass",
        "role": "manager",
        "departments": ["Monitoring & Alerts", "Infrastructure"],
    },
    {
        "name": "Demo Technician",
        "email": "technician@example.com",
        "password": "technician-demo-pass",
        "role": "technician",
        "departments": [],
    },
]


def seed_demo_staff(conn) -> None:
    if os.environ.get("APP_ENV", "development").lower() == "production":
        return
    for person in DEMO_STAFF:
        if conn.execute(
            "SELECT 1 FROM staff WHERE email = ?", (person["email"],)
        ).fetchone():
            continue
        prefix = "MGR" if person["role"] == "manager" else "TECH"
        staff_id = f"{prefix}-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO staff (staff_id, name, email, password_hash, role) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                staff_id,
                person["name"],
                person["email"],
                bcrypt.hashpw(person["password"].encode(), bcrypt.gensalt()).decode(),
                person["role"],
            ),
        )
        for dept_name in person["departments"]:
            dept = conn.execute(
                "SELECT department_id FROM departments WHERE name = ?", (dept_name,)
            ).fetchone()
            if dept:
                conn.execute(
                    "INSERT OR IGNORE INTO manager_departments (manager_id, department_id) "
                    "VALUES (?, ?)",
                    (staff_id, dept["department_id"]),
                )
    conn.commit()
