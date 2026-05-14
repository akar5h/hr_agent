"""Audit log writer for decisioning and outreach side effects."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from src.database.db import get_db
from src.observability.decorators import traced

logger = logging.getLogger(__name__)


def _payload_hash(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@traced(name="audit-event")
def record_audit_event(
    *,
    client_id: str,
    tool: str,
    action: str,
    session_id: str = "",
    target_id: str = "",
    payload: dict[str, Any] | None = None,
    outcome: str = "ok",
    error: str = "",
    actor: str = "agent",
) -> None:
    """Insert an audit row for a decisioning or outreach action.

    Failures here must never break the caller — auditing is best-effort and
    the underlying side effect has already been validated by the caller.
    """
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO audit_events
                    (session_id, client_id, actor, tool, action,
                     target_id, payload_hash, outcome, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id or None,
                    client_id,
                    actor,
                    tool,
                    action,
                    target_id or None,
                    _payload_hash(payload),
                    outcome,
                    error or None,
                ),
            )
    except Exception as exc:  # pragma: no cover - exercised via logging
        logger.warning("audit_event_write_failed", extra={"tool": tool, "error": str(exc)})
