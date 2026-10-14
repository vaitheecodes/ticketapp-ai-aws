"""Mock `orders` data — seeded once so generate_response's order-lookup
grounding (backend/graph.py:lookup_order) has real records to answer
from, instead of inventing plausible-sounding order details.

Faker-driven, fixed seed, same convention as src/pipeline/02_silver_transform.py
(SEED = 42) for reproducibility. Idempotent: skipped if `orders` already
has rows, so repeated `init_db()` calls (every app startup) don't
duplicate or reset data a tester may have relied on.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

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
