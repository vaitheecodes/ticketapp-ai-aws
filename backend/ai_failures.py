"""AI failures on a chat or ticket, recorded so they can be seen without a
terminal (admin only).

Every AIFailure of a turn (provider.start_failure_log) is written to the
audit log as `chat.ai_failure` / `ticket.ai_failure`: kind, error code
name, attempts, latency, operation and outcome ("failed" showed the
customer the "AI unavailable" message; "regenerated" / "fallback" were
rejected by our own checks and recovered; "filtered": the reply was sent
without the steps the KB doesn't support, code ungrounded_steps). Never provider message text,
secrets or customer text. GET /api/admin/chats/{id} and the admin
Activity tab show them.
"""

from __future__ import annotations

import json
import logging

from backend import audit
from backend import ticket_lifecycle as lifecycle
from backend.ai_service import AIError, AIFailure, backends

logger = logging.getLogger(__name__)


def note_exception(exc: BaseException, log: list, operation: str = "turn") -> None:
    """Add a failure for `exc` unless it already carries one in `log`
    (anything that isn't a described AIError is a bug: kind unknown, the
    exception's class name as the code)."""
    failure = exc.failure if isinstance(exc, AIError) else None
    if failure is None:
        log.append(AIFailure(kind="unknown", operation=operation,
                             code=backends.safe_code(type(exc).__name__)))
    elif not any(f is failure for f in log):
        log.append(failure)


def record(conn, entity: str, entity_id: str, failures: list) -> None:
    """Write the failures; never raises (a turn must not fail because its
    failure record couldn't be written). Caller commits."""
    try:
        for failure in failures:
            audit.record(conn, lifecycle.SYSTEM, f"{entity}.ai_failure", entity, entity_id,
                         **failure.detail())
    except Exception:  # noqa: BLE001
        logger.exception("Recording AI failures for %s %s failed", entity, entity_id)


def for_entity(conn, entity: str, entity_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT at, detail FROM audit_events WHERE entity = ? AND entity_id = ? AND action = ? "
        "ORDER BY id",
        (entity, entity_id, f"{entity}.ai_failure"),
    ).fetchall()
    return [{"at": r["at"], **json.loads(r["detail"])} for r in rows]
