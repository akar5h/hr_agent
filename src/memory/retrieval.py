"""Semantic memory retrieval with cosine similarity scoring."""

from __future__ import annotations

import json
import math
from typing import Any

TOP_K = 5


def _char_ngram_embedding(text: str, n: int = 3) -> list[float]:
    text = text.lower()[:500]
    ngrams: dict[str, int] = {}
    for index in range(len(text) - n + 1):
        gram = text[index : index + n]
        ngrams[gram] = ngrams.get(gram, 0) + 1

    vector = [0.0] * 128
    for gram, count in ngrams.items():
        bucket = hash(gram) % 128
        vector[bucket] += count

    magnitude = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [value / magnitude for value in vector]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    try:
        import numpy as np

        return float(np.dot(a, b))
    except ImportError:
        return sum(x * y for x, y in zip(a, b))


def retrieve_relevant_memories(
    conn: Any,
    client_id: str,
    query_context: str,
    memory_type: str = "semantic",
    top_k: int = TOP_K,
) -> list[dict]:
    """Retrieve most relevant non-expired memories for a client."""
    rows = conn.execute(
        """
        SELECT
            id,
            memory_key,
            memory_value,
            embedding,
            access_count
        FROM agent_memory
        WHERE client_id = %s
          AND memory_type = %s
          AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY updated_at DESC, created_at DESC
        """,
        (client_id, memory_type),
    ).fetchall()
    if not rows:
        return []

    query_vector = _char_ngram_embedding(query_context or client_id)
    scored: list[tuple[float, dict]] = []

    for row in rows:
        memory = dict(row)
        raw_embedding = memory.get("embedding")
        similarity = 0.0
        if raw_embedding:
            try:
                parsed = json.loads(str(raw_embedding))
                if isinstance(parsed, list):
                    similarity = _cosine_similarity(query_vector, parsed)
            except (json.JSONDecodeError, TypeError, ValueError):
                similarity = 0.0
        scored.append((similarity, memory))

    scored.sort(key=lambda item: item[0], reverse=True)
    selected = [memory for _, memory in scored[:top_k]]

    memory_ids = [item["id"] for item in selected if item.get("id") is not None]
    if memory_ids:
        placeholders = ", ".join(["%s"] * len(memory_ids))
        conn.execute(
            f"""
            UPDATE agent_memory
            SET access_count = access_count + 1, updated_at = NOW()
            WHERE id IN ({placeholders})
            """,
            memory_ids,
        )
    return selected
