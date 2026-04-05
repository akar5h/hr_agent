"""Tools for persisting and retrieving agent memory."""

from __future__ import annotations

import json
import math
from typing import Optional

from pydantic import BaseModel

from src.database.db import get_db
from src.memory.retrieval import retrieve_relevant_memories
from src.observability.decorators import traced
from src.tools._compat import tool


def _embedding_for_text(text: str) -> str:
    n = 3
    normalized = text.lower()[:500]
    ngrams: dict[str, int] = {}
    for idx in range(len(normalized) - n + 1):
        gram = normalized[idx : idx + n]
        ngrams[gram] = ngrams.get(gram, 0) + 1

    vector = [0.0] * 128
    for gram, count in ngrams.items():
        vector[hash(gram) % 128] += count
    magnitude = math.sqrt(sum(v * v for v in vector)) or 1.0
    normalized_vector = [value / magnitude for value in vector]
    return json.dumps(normalized_vector)


def _classify_memory_type(memory_key: str) -> str:
    if memory_key.startswith("client_pref:") or memory_key.startswith("consolidated:"):
        return "semantic"
    return "episodic"


@traced(name="load-client-memories")
def _load_client_memories(
    client_id: str,
    query_context: str = "",
    limit: int = 5,
) -> list[dict]:
    """Load top semantically relevant client preferences for prompt injection."""
    try:
        with get_db() as conn:
            memories = retrieve_relevant_memories(
                conn=conn,
                client_id=client_id,
                query_context=query_context or client_id,
                memory_type="semantic",
                top_k=limit,
            )
        return [{"memory_key": m["memory_key"], "memory_value": m["memory_value"]} for m in memories]
    except Exception:
        return []


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
    memory_type = _classify_memory_type(memory_key)
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO agent_memory (
                    session_id,
                    client_id,
                    memory_key,
                    memory_value,
                    memory_type,
                    expires_at,
                    embedding,
                    access_count,
                    updated_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    CASE WHEN %s = 'episodic' THEN NOW() + INTERVAL '30 days' ELSE NULL END,
                    %s,
                    0,
                    NOW()
                )
                """,
                (
                    session_id,
                    client_id,
                    memory_key,
                    memory_value,
                    memory_type,
                    memory_type,
                    _embedding_for_text(memory_value),
                ),
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
                    """
                    SELECT memory_key, memory_value, memory_type, created_at
                    FROM agent_memory
                    WHERE session_id = %s
                      AND client_id = %s
                      AND memory_key = %s
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY created_at DESC
                    LIMIT 20
                    """,
                    (session_id, client_id, memory_key),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT memory_key, memory_value, memory_type, created_at
                    FROM agent_memory
                    WHERE session_id = %s
                      AND client_id = %s
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY created_at DESC
                    LIMIT 20
                    """,
                    (session_id, client_id),
                ).fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:
        return [{"error": str(exc)}]
