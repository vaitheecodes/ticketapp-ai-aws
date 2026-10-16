"""Audit log: who did what, when.

record() is called from the places that change state an admin may need to
account for — ticket status/owner changes and claims (via
ticket_lifecycle), incident reviews and department corrections, staff /
customer / department / settings administration, and failed logins.

`detail` must stay small and safe: ids, statuses, names, field names. Never
message bodies, notes, passwords, tokens or AI prompts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

ANONYMOUS = "anonymous"


def record(conn, actor_id: str | None, action: str, entity: str, entity_id: str | None = None,
           **detail) -> None:
    conn.execute(
        "INSERT INTO audit_events (actor_id, action, entity, entity_id, detail, at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (actor_id or ANONYMOUS, action, entity, entity_id, json.dumps(detail, default=str),
         datetime.now(timezone.utc).isoformat()),
    )
