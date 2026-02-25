"""TTL cleanup helpers for episodic memory rows."""

from __future__ import annotations

from typing import Any


def expire_old_memories(conn: Any) -> int:
    """Delete expired episodic memories and return deleted row count."""
    deleted_rows = conn.execute(
        """
        DELETE FROM agent_memory
        WHERE memory_type = 'episodic'
          AND expires_at IS NOT NULL
          AND expires_at < NOW()
        RETURNING id
        """
    ).fetchall()
    return len(deleted_rows)
