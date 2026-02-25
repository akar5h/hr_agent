"""Per-session tool call rate limits for hardening mode."""

from __future__ import annotations

import os
from collections import defaultdict

ENABLE_HARDENING = os.getenv("ENABLE_HARDENING", "false").lower() == "true"
MAX_TOOL_CALLS = int(os.getenv("MAX_TOOL_CALLS_PER_SESSION", "50"))

_session_counts: dict[str, int] = defaultdict(int)


class ToolRateLimitError(RuntimeError):
    """Raised when a session exceeds configured tool call limits."""


def record_tool_call(session_id: str) -> None:
    if not ENABLE_HARDENING:
        return
    _session_counts[session_id] += 1
    if _session_counts[session_id] > MAX_TOOL_CALLS:
        raise ToolRateLimitError(f"Session {session_id} exceeded {MAX_TOOL_CALLS} tool calls.")


def get_call_count(session_id: str) -> int:
    return _session_counts.get(session_id, 0)


def reset_session(session_id: str) -> None:
    _session_counts.pop(session_id, None)
