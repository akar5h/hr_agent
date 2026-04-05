"""Consolidate episodic memories into semantic client preferences."""

from __future__ import annotations

import json
from typing import Any

from src.observability.decorators import traced

CONSOLIDATION_PROMPT_TEMPLATE = """
You are a memory consolidation assistant. Review the following episodic session notes
for client {client_id} and extract 1-3 durable, generalizable facts about their
hiring preferences.

Rules:
- Each fact must be a single sentence.
- Facts must be generalizable (not specific to one candidate).
- Discard one-off observations.
- Return ONLY a JSON list of strings.

Episodic notes:
{notes}
"""


@traced(name="consolidate-session-memories")
def consolidate_session_memories(conn: Any, model: Any, client_id: str, session_id: str) -> list[str]:
    """Write up to 3 consolidated semantic memory entries and return them."""
    rows = conn.execute(
        """
        SELECT memory_key, memory_value
        FROM agent_memory
        WHERE client_id = %s
          AND memory_type = 'episodic'
          AND session_id = %s
          AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (client_id, session_id),
    ).fetchall()
    if not rows:
        return []

    notes = "\n".join(f"- {row['memory_key']}: {row['memory_value']}" for row in rows)
    prompt = CONSOLIDATION_PROMPT_TEMPLATE.format(client_id=client_id, notes=notes)

    try:
        response = model.invoke(prompt)
        raw = getattr(response, "content", str(response)).strip()
        facts = json.loads(raw)
        if not isinstance(facts, list):
            return []
    except Exception:
        return []

    written: list[str] = []
    for index, fact in enumerate(facts[:3]):
        if not isinstance(fact, str) or not fact.strip():
            continue
        memory_key = f"consolidated:{session_id}:{index}"
        conn.execute(
            """
            INSERT INTO agent_memory (
                session_id,
                client_id,
                memory_key,
                memory_value,
                memory_type,
                expires_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, 'semantic', NULL, NOW())
            """,
            (session_id, client_id, memory_key, fact.strip()),
        )
        written.append(fact.strip())
    return written
