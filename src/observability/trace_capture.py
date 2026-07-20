"""Per-session sub-agent span capture for the trace exporter.

Leaf module (no project imports) so any tool / sub-agent can record into it without
circular-import risk. Observability ONLY — nothing here is read by the agent or changes
a decision. Buffers are keyed by session_id (each traffic-harness scenario uses a fresh
session), accumulate across the scenario's turns, and are drained by the harness.
"""

from __future__ import annotations

from typing import Any

_SUB_AGENTS: dict[str, list[dict[str, Any]]] = {}


def record_sub_agent(session_id: str | None, entry: dict[str, Any]) -> None:
    """Append one sub-agent / sub-model span for a session (best-effort, never raises)."""
    if not session_id:
        return
    try:
        _SUB_AGENTS.setdefault(session_id, []).append(entry)
    except Exception:
        pass


def get_sub_agents(session_id: str | None) -> list[dict[str, Any]]:
    """Return the sub-agent spans captured for a session."""
    if not session_id:
        return []
    return list(_SUB_AGENTS.get(session_id, []))


def reset_sub_agents(session_id: str | None) -> None:
    """Drop a session's captured sub-agent spans (call at scenario end)."""
    if session_id:
        _SUB_AGENTS.pop(session_id, None)
