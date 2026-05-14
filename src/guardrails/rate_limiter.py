"""Per-session tool call rate limits for hardening mode."""

from __future__ import annotations

import os
from collections import defaultdict

from src.observability.decorators import traced

ENABLE_HARDENING = os.getenv("ENABLE_HARDENING", "false").lower() == "true"
MAX_TOOL_CALLS = int(os.getenv("MAX_TOOL_CALLS_PER_SESSION", "50"))

# Per-tool, per-session quotas. Tighter than the session-wide cap to bound
# blast radius on the decisioning + outreach paths even when the session
# limit is generous.
DEFAULT_PER_TOOL_LIMITS: dict[str, int] = {
    "send_candidate_email": int(os.getenv("MAX_SEND_EMAIL_PER_SESSION", "5")),
    "shortlist_candidate": int(os.getenv("MAX_SHORTLIST_PER_SESSION", "20")),
    "reject_candidate": int(os.getenv("MAX_REJECT_PER_SESSION", "20")),
}

_session_counts: dict[str, int] = defaultdict(int)
_per_tool_counts: dict[tuple[str, str], int] = defaultdict(int)


class ToolRateLimitError(RuntimeError):
    """Raised when a session exceeds configured tool call limits."""


@traced(name="record-tool-call")
def record_tool_call(session_id: str, tool_name: str | None = None) -> None:
    if not ENABLE_HARDENING:
        return
    _session_counts[session_id] += 1
    if _session_counts[session_id] > MAX_TOOL_CALLS:
        raise ToolRateLimitError(f"Session {session_id} exceeded {MAX_TOOL_CALLS} tool calls.")

    if tool_name and tool_name in DEFAULT_PER_TOOL_LIMITS:
        key = (session_id, tool_name)
        _per_tool_counts[key] += 1
        limit = DEFAULT_PER_TOOL_LIMITS[tool_name]
        if _per_tool_counts[key] > limit:
            raise ToolRateLimitError(
                f"Session {session_id} exceeded {tool_name} quota ({limit} per session)."
            )


def enforce_per_tool_limit(session_id: str, tool_name: str) -> None:
    """Always-on per-tool quota check independent of ENABLE_HARDENING.

    Decisioning and outreach tools must respect their per-tool budget even when
    the broad hardening flag is off, since they cause side effects on humans.
    """
    if tool_name not in DEFAULT_PER_TOOL_LIMITS:
        return
    key = (session_id, tool_name)
    _per_tool_counts[key] += 1
    limit = DEFAULT_PER_TOOL_LIMITS[tool_name]
    if _per_tool_counts[key] > limit:
        raise ToolRateLimitError(
            f"Session {session_id} exceeded {tool_name} quota ({limit} per session)."
        )


def get_call_count(session_id: str) -> int:
    return _session_counts.get(session_id, 0)


def get_per_tool_count(session_id: str, tool_name: str) -> int:
    return _per_tool_counts.get((session_id, tool_name), 0)


def reset_session(session_id: str) -> None:
    _session_counts.pop(session_id, None)
    stale = [key for key in _per_tool_counts if key[0] == session_id]
    for key in stale:
        _per_tool_counts.pop(key, None)
