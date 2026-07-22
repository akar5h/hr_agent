"""Session-scoped exact-key dedup for tool calls that get re-issued reworded.

Existing caches (`ToolCache`, `cached_tool`) key on the raw natural-language
input, so an agent that rewords the same intent ("list open positions" vs
"get positions with status open") bypasses them and re-executes the same
underlying query. This module dedups on a caller-supplied *normalized* key
(e.g. the generated SQL, or a normalized search string) within one session,
so semantically-identical repeats are caught even when the wording differs.

Never breaks the caller: lookups on an unknown/missing session or key just
report "not seen", and callers are expected to fall through to real
execution on any failure here.
"""

from __future__ import annotations

from typing import Any

from src.observability.logging import get_logger

logger = get_logger(__name__)

# session_id -> key -> {"result": Any, "count": int}
_STORE: dict[str, dict[str, dict[str, Any]]] = {}


def seen(session_id: str | None, key: str) -> bool:
    """True if `key` was already recorded for `session_id` this session."""
    if not session_id:
        return False
    return key in _STORE.get(session_id, {})


def get(session_id: str | None, key: str) -> Any | None:
    """Return the stored result for `key`, or None if not recorded."""
    if not session_id:
        return None
    entry = _STORE.get(session_id, {}).get(key)
    return entry["result"] if entry else None


def put(session_id: str | None, key: str, result: Any) -> None:
    """Store the first-seen result for `key` (count starts at 1)."""
    if not session_id:
        return
    _STORE.setdefault(session_id, {})[key] = {"result": result, "count": 1}


def bump(session_id: str | None, key: str) -> int:
    """Increment the repeat count for `key` and return the new count."""
    if not session_id:
        return 0
    entry = _STORE.setdefault(session_id, {}).get(key)
    if entry is None:
        # Nothing recorded yet — nothing to bump; treat as a first sighting.
        entry = {"result": None, "count": 0}
        _STORE[session_id][key] = entry
    entry["count"] += 1
    return entry["count"]


def reset(session_id: str | None) -> None:
    """Drop all dedup state for `session_id` (e.g. at scenario/session end)."""
    if not session_id:
        return
    _STORE.pop(session_id, None)
    logger.info("session_dedup: reset session_id=%s", session_id)
