"""Tools for persisting and retrieving agent memory."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from src.database.db import get_db
from src.tools._compat import tool


class StoreMemoryInput(BaseModel):
    """Input schema for store_memory."""

    session_id: str
    client_id: str
    memory_key: str
    memory_value: str


class RetrieveMemoryInput(BaseModel):
    """Input schema for retrieve_memory."""

    session_id: str
    client_id: str
    memory_key: Optional[str] = None


@tool(args_schema=StoreMemoryInput)
def store_memory(session_id: str, client_id: str, memory_key: str, memory_value: str) -> dict:
    """Store a memory entry for the current session."""
    try:
        with get_db() as conn:
            conn.execute(
                (
                    "INSERT INTO agent_memory (session_id, client_id, memory_key, memory_value) "
                    "VALUES (?, ?, ?, ?)"
                ),
                (session_id, client_id, memory_key, memory_value),
            )
        return {"success": True, "memory_key": memory_key}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@tool(args_schema=RetrieveMemoryInput)
def retrieve_memory(session_id: str, client_id: str, memory_key: Optional[str] = None) -> list[dict]:
    """Retrieve memory entries for the current session."""
    try:
        with get_db() as conn:
            if memory_key is not None:
                rows = conn.execute(
                    (
                        "SELECT memory_key, memory_value, created_at "
                        "FROM agent_memory WHERE session_id = ? AND client_id = ? AND memory_key = ?"
                    ),
                    (session_id, client_id, memory_key),
                ).fetchall()
            else:
                rows = conn.execute(
                    (
                        "SELECT memory_key, memory_value, created_at "
                        "FROM agent_memory WHERE session_id = ? AND client_id = ?"
                    ),
                    (session_id, client_id),
                ).fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:
        return [{"error": str(exc)}]
