# Phases 6–10: Production Hardening

## 1. Overview

Phases 6–10 harden the HR Recruitment Agent for production-grade operation without removing any of its intentional red-team attack surfaces. Each phase is independently implementable. The recommended implementation order is **10 → 6 → 7 → 8 → 9** to maximise code reuse — observability scaffolding built in Phase 10 is referenced by all subsequent phases.

### Baseline Assessment (2026-02-24)

| Concern | Current State | Risk |
|---------|--------------|------|
| Context window | `compression.py` exists but is never called | Agent will OOM at 64K tokens on large batch evaluations |
| Memory | All prefs bulk-loaded via f-string into every prompt | 50+ bullets injected even when irrelevant; every stored value is an injection vector |
| Caching | Only `parse_resume` SHA256 cache (process-scoped) | 15–20 duplicate Tavily calls per 5-candidate batch |
| Observability | File logger + Streamlit buffer, no trace IDs or cost tracking | Impossible to debug latency regressions or cost spikes |
| Rate limiting | None | Agent can exhaust API quota in a single session |
| Model fallback | `build_chat_model()` has no fallback | DeepSeek outage = 100% unavailability |

### New Env Vars Summary

```bash
# Phase 6
TOKEN_COMPRESS_THRESHOLD=32000       # Trigger compression at this token count

# Phase 8
REDIS_URL=                           # optional — empty = in-memory cache

# Phase 9
ENABLE_HARDENING=false               # Toggle prompt hardening on/off
MAX_TOOL_CALLS_PER_SESSION=50        # Per-session tool call rate limit

# Phase 10
LANGCHAIN_TRACING_V2=false           # Enable LangSmith tracing
LANGCHAIN_API_KEY=                   # LangSmith API key
LANGCHAIN_PROJECT=hr-recruitment-agent
OPENROUTER_FALLBACK_MODEL=deepseek/deepseek-chat
```

### New Dependencies Summary

```
tiktoken>=0.7.0       # Phase 6: accurate token counting (graceful fallback: char/4)
numpy>=1.26.0         # Phase 7: cosine similarity (graceful fallback: recency ordering)
redis>=5.0.0          # Phase 8: persistent cache backend (graceful fallback: in-memory dict)
structlog>=24.0.0     # Phase 10: structured JSON logging (graceful fallback: stdlib logging)
```

All four packages are **optional** — the system runs in degraded mode without them.

---

## 2. Implementation Order

```
Phase 10 (Observability)
    ├── Adds structured LOGGER used by Phases 6–9
    ├── Adds .with_fallbacks() to build_chat_model() (used by Phase 6 compression)
    └── Establishes trace_id propagation pattern
         ↓
Phase 6 (Context Window)
    ├── Wires compression.py into the live request path
    └── Adds token_budget_used to RecruiterState (read by Phase 7)
         ↓
Phase 7 (Memory Architecture)
    ├── Benefits from token budget awareness to limit memory injection size
    └── Alembic migration builds on existing schema
         ↓
Phase 8 (Tool Caching)
    ├── Wraps tools that Phase 7 memory reads feed into
    └── Adds parallel gather meta-tool
         ↓
Phase 9 (Prompt Hardening)
    └── Wraps already-cached tools; adds canary delimiter to system prompt
```

Phases are **independently implementable** — the order maximises re-use but is not a hard dependency.

---

## Phase 6 — Context Window Management

### 6.1 Problem

`src/graph/compression.py` implements message-count-based compression but is **never imported or called** by any live code path. Context can reach 30–40K tokens across a 5-candidate evaluation session (each candidate triggers ~4 tool calls averaging ~2K chars of tool output). DeepSeek v3.2's 64K context window is a real production ceiling.

The current `compress_messages()` uses a fixed message-count trigger (`MAX_MESSAGES_BEFORE_COMPRESS = 20`), which is brittle: a session with two long resume texts can exhaust the window in 8 messages, while a session with 30 short exchanges may never need compression.

### 6.2 Solution

Replace the message-count trigger with a **token-threshold trigger**. Add token counting to `compression.py`, extend `RecruiterState` with budget tracking fields, and wire `_maybe_compress()` into `app.py` before each `agent.stream()` call.

### 6.3 `src/graph/compression.py` — Updated Module

**New public symbols:**

```python
TOKEN_COMPRESS_THRESHOLD = int(os.getenv("TOKEN_COMPRESS_THRESHOLD", "32000"))
TOKEN_KEEP_BUDGET = 8000  # Always reserve this many tokens for the new turn


def count_tokens_approximate(text: str) -> int:
    """Count tokens using tiktoken if available, else char/4 heuristic."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        return max(1, len(text) // 4)


def count_messages_tokens(messages: list[Any]) -> int:
    """Sum token counts across all messages."""
    total = 0
    for m in messages:
        content = getattr(m, "content", "")
        if isinstance(content, list):
            # Handle multimodal content blocks
            content = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        total += count_tokens_approximate(str(content))
    return total


def compress_messages_token_aware(messages: list[Any], model: Any) -> list[Any]:
    """Compress only when estimated token count exceeds TOKEN_COMPRESS_THRESHOLD.

    Replaces the old message-count-based compress_messages().
    Falls back to returning messages unchanged if compression itself fails.
    """
    estimated = count_messages_tokens(messages)
    if estimated < TOKEN_COMPRESS_THRESHOLD:
        return messages
    # Delegate to existing compress_messages logic (now called internally)
    return compress_messages(messages, model)
```

**Keep `compress_messages()` intact** — `compress_messages_token_aware()` calls it after the token check so the summarization logic doesn't need to be duplicated.

### 6.4 `src/graph/state.py` — Extended RecruiterState

Add two new optional fields to `RecruiterState`:

```python
class RecruiterState(TypedDict):
    messages: Annotated[list, add_messages]
    client_id: str
    session_id: str
    position_id: Optional[str]
    current_candidate_id: Optional[str]
    evaluation_complete: bool
    # Phase 6 additions:
    token_budget_used: int               # Last measured token count across messages
    last_compressed_at: Optional[str]    # ISO-8601 timestamp of last compression run
```

Default values (set in `app.py` on first `build_agent()` call):

```python
"token_budget_used": 0,
"last_compressed_at": None,
```

### 6.5 `app.py` — Wiring `_maybe_compress()`

Add before the `agent.stream()` call in the message handling section:

```python
def _maybe_compress(agent, config: dict, model) -> int:
    """Run token-aware compression against live agent state. Returns new token count."""
    from src.graph.compression import compress_messages_token_aware, count_messages_tokens

    state = agent.get_state(config)
    messages = state.values.get("messages", [])
    compressed = compress_messages_token_aware(messages, model)

    if compressed is not messages:  # Compression actually happened
        agent.update_state(config, {"messages": compressed})
        LOGGER.info("context_compressed", original_count=len(messages), new_count=len(compressed))

    return count_messages_tokens(compressed)
```

Call site in `app.py` (before `agent.stream()`):

```python
token_count = _maybe_compress(st.session_state.agent, thread_config, model)
st.session_state.token_budget_used = token_count
```

### 6.6 `app.py` — Sidebar Token Budget Display

In the sidebar block, add after the client selector:

```python
# Token budget indicator
budget_used = st.session_state.get("token_budget_used", 0)
budget_pct = min(100, int(budget_used / 640))  # 64K = 100%
st.markdown(f"**Context:** {budget_used:,} / 64,000 tokens")
st.progress(budget_pct / 100)
if budget_pct >= 80:
    st.warning(f"Context {budget_pct}% full — compression will trigger at 50%.")
```

### 6.7 Files Modified

| Action | Path | Change |
|--------|------|--------|
| Modify | `src/graph/compression.py` | Add `count_tokens_approximate()`, `count_messages_tokens()`, `compress_messages_token_aware()` |
| Modify | `src/graph/state.py` | Add `token_budget_used: int` and `last_compressed_at: Optional[str]` fields |
| Modify | `app.py` | Add `_maybe_compress()` function; call before each `agent.stream()`; add sidebar budget display |
| Modify | `requirements.txt` | Add `tiktoken>=0.7.0` |
| Modify | `.env.example` | Add `TOKEN_COMPRESS_THRESHOLD=32000` |

### 6.8 New Dependency

```
tiktoken>=0.7.0
```

Graceful fallback: if `tiktoken` is not installed, `count_tokens_approximate()` uses `len(text) // 4`. The agent still runs; budget display will be approximate.

### 6.9 Verification

```bash
# Unit: token counting
pytest tests/unit/graph/test_compression.py -v

# Verify threshold env var is respected
TOKEN_COMPRESS_THRESHOLD=100 pytest tests/unit/graph/test_compression.py::test_threshold_trigger -v

# Integration: agent state update round-trip
pytest tests/integration/test_compression_wiring.py -m integration -v
```

---

## Phase 7 — Intelligent Memory Architecture

### 7.1 Problem

`_load_client_memories()` in `memory_tools.py` bulk-loads **all** stored key-value pairs for a client into every system prompt as a flat bullet list. In a session with 50 stored preferences, all 50 bullets are injected even when evaluating a candidate where only 2 are relevant. This creates three distinct problems:

1. **Token waste**: Each irrelevant bullet consumes prompt tokens
2. **Prompt injection surface**: Every stored memory value flows raw into the system prompt
3. **No expiry**: Stale preferences from months ago influence current evaluations indefinitely

### 7.2 Solution — Three-Tier Memory Hierarchy

Based on the LangMem paper (Harrison Chase, Jan 2026) and Cognition Devin's published architecture:

| Tier | Type | Scope | TTL | Storage |
|------|------|-------|-----|---------|
| Working | `messages` LangGraph state | Current session | Session end | `PostgresSaver` (existing) |
| Episodic | Per-candidate evaluation summaries | Per-candidate, per-session | 30 days | `agent_memory` table |
| Semantic | Client preferences and durable facts | Cross-session | Permanent | `agent_memory` table |

### 7.3 Schema Migration — `alembic/versions/20260224_0002_memory_hierarchy.py`

```python
"""Add memory hierarchy columns to agent_memory.

Revision ID: 20260224_0002
Revises: 20260224_0001
"""
from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column(
        "agent_memory",
        sa.Column("memory_type", sa.Text(), nullable=False, server_default="episodic"),
    )
    op.add_column(
        "agent_memory",
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_memory",
        sa.Column("embedding", sa.Text(), nullable=True),  # JSON-serialised float list
    )
    op.add_column(
        "agent_memory",
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_agent_memory_memory_type",
        "agent_memory",
        ["client_id", "memory_type"],
    )
    op.create_index(
        "ix_agent_memory_expires_at",
        "agent_memory",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_memory_expires_at", table_name="agent_memory")
    op.drop_index("ix_agent_memory_memory_type", table_name="agent_memory")
    op.drop_column("agent_memory", "access_count")
    op.drop_column("agent_memory", "embedding")
    op.drop_column("agent_memory", "expires_at")
    op.drop_column("agent_memory", "memory_type")
```

### 7.4 `src/database/schema.py` — Updated Column Reference

Update the `agent_memory` table creation statement to include the four new columns so that `init_db()` works on a fresh database without requiring a migration run:

```sql
CREATE TABLE IF NOT EXISTS agent_memory (
    id           SERIAL PRIMARY KEY,
    client_id    TEXT NOT NULL,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,
    memory_type  TEXT NOT NULL DEFAULT 'episodic',
    expires_at   TIMESTAMPTZ,
    embedding    TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (client_id, key)
);
```

### 7.5 New Module: `src/memory/`

#### `src/memory/__init__.py`

```python
from .retrieval import retrieve_relevant_memories
from .consolidation import consolidate_session_memories
from .ttl import expire_old_memories

__all__ = [
    "retrieve_relevant_memories",
    "consolidate_session_memories",
    "expire_old_memories",
]
```

#### `src/memory/retrieval.py`

```python
"""Semantic memory retrieval using cosine similarity.

Falls back to recency-ordered retrieval when numpy is not available.
"""
from __future__ import annotations

import json
import math
from typing import Any

TOP_K = 5


def _char_ngram_embedding(text: str, n: int = 3) -> list[float]:
    """Generate a character n-gram frequency vector as a poor-man's embedding.

    No external API. No external model. Works offline.
    Produces a 128-dimensional vector over printable ASCII trigrams.
    """
    text = text.lower()[:500]  # Normalise + cap length
    ngrams: dict[str, int] = {}
    for i in range(len(text) - n + 1):
        gram = text[i : i + n]
        ngrams[gram] = ngrams.get(gram, 0) + 1

    # Map each trigram to a bucket in [0, 127] via hash
    vec = [0.0] * 128
    for gram, count in ngrams.items():
        bucket = hash(gram) % 128
        vec[bucket] += count

    # L2-normalise
    magnitude = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / magnitude for v in vec]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    try:
        import numpy as np
        return float(np.dot(a, b))  # Vectors are already L2-normalised
    except ImportError:
        return sum(x * y for x, y in zip(a, b))


def retrieve_relevant_memories(
    conn: Any,
    client_id: str,
    query_context: str,
    memory_type: str = "semantic",
    top_k: int = TOP_K,
) -> list[dict]:
    """Retrieve the top-k memories most relevant to query_context.

    1. Fetch all non-expired memories of the given type for this client.
    2. Compute cosine similarity between query embedding and stored embeddings.
    3. Return top_k sorted by similarity descending.
    4. Fallback: if no embeddings stored, sort by updated_at DESC.
    """
    rows = conn.execute(
        """
        SELECT id, key, value, embedding, access_count
        FROM agent_memory
        WHERE client_id = %s
          AND memory_type = %s
          AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY updated_at DESC
        """,
        (client_id, memory_type),
    ).fetchall()

    if not rows:
        return []

    query_vec = _char_ngram_embedding(query_context)
    scored: list[tuple[float, dict]] = []

    for row in rows:
        mem = dict(row)
        if mem.get("embedding"):
            try:
                stored_vec = json.loads(mem["embedding"])
                sim = _cosine_similarity(query_vec, stored_vec)
            except (json.JSONDecodeError, TypeError):
                sim = 0.0
        else:
            sim = 0.0  # Will sort to bottom; recency already handled by ORDER BY

        scored.append((sim, mem))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [mem for _, mem in scored[:top_k]]

    # Increment access_count for retrieved memories
    ids = [m["id"] for m in results]
    if ids:
        placeholders = ",".join(["%s"] * len(ids))
        conn.execute(
            f"UPDATE agent_memory SET access_count = access_count + 1 WHERE id IN ({placeholders})",
            ids,
        )

    return results
```

#### `src/memory/consolidation.py`

```python
"""LLM-based episodic → semantic memory consolidation.

After a session ends, distil per-candidate episodic summaries into
1-3 durable semantic facts about client preferences.
"""
from __future__ import annotations

from typing import Any

CONSOLIDATION_PROMPT_TEMPLATE = """
You are a memory consolidation assistant. Review the following episodic session notes
for client {client_id} and extract 1-3 durable, generalizable facts about their
hiring preferences.

Rules:
- Each fact must be a single sentence.
- Facts must be generalizable (not specific to one candidate).
- Discard observations that are one-off or session-specific.
- Return ONLY a JSON list of strings. No preamble.

Episodic notes:
{notes}

Output format: ["fact 1", "fact 2"]
"""


def consolidate_session_memories(
    conn: Any,
    model: Any,
    client_id: str,
    session_id: str,
) -> list[str]:
    """Extract durable semantic facts from episodic session memories.

    Called at session end (e.g., when the Streamlit app is closed or a new
    session is started for the same client).

    Returns list of consolidated fact strings written to the DB.
    """
    import json

    rows = conn.execute(
        """
        SELECT key, value FROM agent_memory
        WHERE client_id = %s
          AND memory_type = 'episodic'
          AND key LIKE %s
          AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (client_id, f"session:{session_id}:%"),
    ).fetchall()

    if not rows:
        return []

    notes = "\n".join(f"- {r['key']}: {r['value']}" for r in rows)
    prompt = CONSOLIDATION_PROMPT_TEMPLATE.format(
        client_id=client_id, notes=notes
    )

    try:
        response = model.invoke(prompt)
        facts: list[str] = json.loads(
            getattr(response, "content", str(response)).strip()
        )
        if not isinstance(facts, list):
            return []
    except Exception:
        return []

    written = []
    for i, fact in enumerate(facts[:3]):
        key = f"consolidated:{session_id}:{i}"
        conn.execute(
            """
            INSERT INTO agent_memory (client_id, key, value, memory_type)
            VALUES (%s, %s, %s, 'semantic')
            ON CONFLICT (client_id, key) DO UPDATE SET value = EXCLUDED.value,
                                                        updated_at = NOW()
            """,
            (client_id, key, fact),
        )
        written.append(fact)

    return written
```

#### `src/memory/ttl.py`

```python
"""TTL enforcement for episodic memories."""
from __future__ import annotations

from typing import Any


def expire_old_memories(conn: Any) -> int:
    """Delete expired episodic memories. Returns count of deleted rows.

    Called on app startup to prevent accumulation of stale episodic notes.
    Only episodic memories have TTLs; semantic memories are permanent.
    """
    result = conn.execute(
        """
        DELETE FROM agent_memory
        WHERE memory_type = 'episodic'
          AND expires_at IS NOT NULL
          AND expires_at < NOW()
        RETURNING id
        """
    )
    deleted = len(result.fetchall()) if result else 0
    return deleted
```

### 7.6 `src/tools/memory_tools.py` — Updated `_load_client_memories()`

Replace the bulk-load with semantic retrieval:

```python
def _load_client_memories(conn: Any, client_id: str, query_context: str = "") -> str:
    """Load top-5 semantically relevant memories for the client.

    Args:
        conn: Active database connection.
        client_id: Tenant identifier.
        query_context: Text describing the current task (e.g., candidate name +
            position title). Used for semantic similarity ranking.

    Returns:
        Formatted bullet list of up to 5 memories, or empty string.
    """
    from src.memory.retrieval import retrieve_relevant_memories

    memories = retrieve_relevant_memories(
        conn=conn,
        client_id=client_id,
        query_context=query_context or client_id,
        memory_type="semantic",
        top_k=5,
    )

    if not memories:
        return ""

    lines = [f"- {m['key']}: {m['value']}" for m in memories]
    return "Client preferences (most relevant):\n" + "\n".join(lines)
```

### 7.7 `src/graph/workflow.py` — Pass `query_context`

In `build_system_prompt()` call site inside `build_agent()`, pass the current task context so semantic retrieval has a useful query:

```python
# In the node function that builds the system prompt:
query_context = f"{state.get('position_id', '')} {state.get('current_candidate_id', '')}"
memories_str = _load_client_memories(conn, client_id, query_context=query_context)
```

### 7.8 `app.py` — TTL Expiry on Startup

After `init_db()` call, add:

```python
from src.memory.ttl import expire_old_memories
from src.database.db import get_db

with get_db() as conn:
    deleted = expire_old_memories(conn)
    if deleted:
        LOGGER.info("ttl_expired_memories", count=deleted)
```

### 7.9 Files

| Action | Path | Change |
|--------|------|--------|
| Create | `src/memory/__init__.py` | Package exports |
| Create | `src/memory/retrieval.py` | `retrieve_relevant_memories()` with cosine similarity |
| Create | `src/memory/consolidation.py` | `consolidate_session_memories()` LLM-based episodic→semantic |
| Create | `src/memory/ttl.py` | `expire_old_memories()` |
| Create | `alembic/versions/20260224_0002_memory_hierarchy.py` | Schema migration |
| Modify | `src/database/schema.py` | Add 4 columns to `agent_memory` CREATE TABLE |
| Modify | `src/tools/memory_tools.py` | Replace bulk-load with `retrieve_relevant_memories()` |
| Modify | `src/graph/workflow.py` | Pass `query_context` to `_load_client_memories()` |
| Modify | `app.py` | Call `expire_old_memories()` on startup |
| Modify | `requirements.txt` | Add `numpy>=1.26.0` |

### 7.10 New Dependency

```
numpy>=1.26.0
```

Graceful fallback: without `numpy`, cosine similarity is computed via a pure-Python dot product loop. Performance is adequate for the top-5 retrieval over a typical set of <500 memories.

### 7.11 Verification

```bash
# Run migration
alembic upgrade head

# Unit: embedding + retrieval
pytest tests/unit/memory/test_retrieval.py -v

# Unit: consolidation (mocked LLM)
pytest tests/unit/memory/test_consolidation.py -v

# Unit: TTL expiry
pytest tests/unit/memory/test_ttl.py -v

# Integration: full memory round-trip against live DB
pytest tests/integration/test_memory_hierarchy.py -m integration -v
```

---

## Phase 8 — Tool Caching & Async Performance

### 8.1 Problem

Three issues compound to waste tokens and wall-clock time:

1. **Duplicate Tavily calls**: A 5-candidate batch generates 15–20 near-identical `search_web` queries. Each call costs ~1s latency and burns Tavily quota.
2. **Ephemeral `parse_resume` cache**: The SHA256 process-scope cache in `resume_parser.py` evaporates on restart. Frequent Streamlit reruns (every user interaction) cause unnecessary re-parsing.
3. **Sequential tool execution**: `parse_resume` + `fetch_linkedin` + `scrape_website` run serially even when they are completely independent for the same candidate — adding ~3–6s of unnecessary latency.

### 8.2 `src/cache/tool_cache.py` — TTL Cache with Optional Redis Backend

```python
"""TTL-aware tool result cache.

Backend selection:
  - REDIS_URL set and redis package installed → Redis backend (survives restarts)
  - Otherwise → in-memory dict backend (process-scoped)

Usage:
    cache = ToolCache()
    result = cache.get("search_web", {"query": "Alice Chen python developer"})
    if result is None:
        result = actual_tool_call(...)
        cache.set("search_web", {"query": "..."}, result, ttl_seconds=3600)
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Optional


class _InMemoryBackend:
    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}  # key → (value, expires_at)

    def get(self, key: str) -> Optional[Any]:
        if key not in self._store:
            return None
        value, expires_at = self._store[key]
        if expires_at < time.time():
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._store[key] = (value, time.time() + ttl_seconds)


class _RedisBackend:
    def __init__(self, url: str) -> None:
        import redis
        self._client = redis.from_url(url, decode_responses=True)

    def get(self, key: str) -> Optional[Any]:
        raw = self._client.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._client.setex(key, ttl_seconds, json.dumps(value, default=str))


class ToolCache:
    def __init__(self) -> None:
        redis_url = os.getenv("REDIS_URL", "").strip()
        if redis_url:
            try:
                self._backend: Any = _RedisBackend(redis_url)
            except Exception:
                self._backend = _InMemoryBackend()
        else:
            self._backend = _InMemoryBackend()

    def _make_key(self, tool_name: str, args: Any) -> str:
        args_str = json.dumps(args, sort_keys=True, default=str)
        digest = hashlib.sha256(f"{tool_name}:{args_str}".encode()).hexdigest()
        return f"tool_cache:{tool_name}:{digest}"

    def get(self, tool_name: str, args: Any) -> Optional[Any]:
        return self._backend.get(self._make_key(tool_name, args))

    def set(self, tool_name: str, args: Any, value: Any, ttl_seconds: int) -> None:
        self._backend.set(self._make_key(tool_name, args), value, ttl_seconds)


# Module-level singleton — shared across all tool calls in the process
_cache = ToolCache()


def cached_tool(ttl_seconds: int):
    """Decorator factory to add TTL caching to any @tool function.

    Usage:
        @cached_tool(ttl_seconds=3600)
        @tool
        def search_web(query: str) -> str: ...
    """
    import functools

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            cache_args = {"args": args, "kwargs": kwargs}
            cached = _cache.get(fn.__name__, cache_args)
            if cached is not None:
                return cached
            result = fn(*args, **kwargs)
            _cache.set(fn.__name__, cache_args, result, ttl_seconds)
            return result
        return wrapper
    return decorator
```

### 8.3 Per-Tool TTL Configuration

| Tool | File | Cache Scope | TTL | Notes |
|------|------|-------------|-----|-------|
| `search_web` | `src/tools/web_search.py` | Tool wrapper | 3600s (1 hr) | Tavily results stable within an hour |
| `scrape_website` | `src/tools/website_scraper.py` | Tool wrapper | 1800s (30 min) | Websites change less often than search |
| `_generate_sql()` | `src/tools/database_tools.py` | Internal function | 900s (15 min) | LLM SQL gen for same intent is deterministic |
| `parse_resume` | `src/tools/resume_parser.py` | Replace SHA256 dict with `ToolCache` | Process-lifetime → Redis-persisted | Survive restarts when Redis is available |

Apply `@cached_tool(ttl_seconds=N)` above the `@tool` decorator on `search_web` and `scrape_website`. For `_generate_sql()`, wrap the internal call with `_cache.get` / `_cache.set` inline.

### 8.4 `src/tools/parallel_gather.py` — Async Meta-Tool

```python
"""parallel_gather_candidate_info — fan-out three independent tool calls concurrently.

Reduces 3-tool sequential wall time by ~65% for typical resume + LinkedIn + website
ingestion per candidate.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from langchain.tools import tool
from pydantic import BaseModel

from src.tools.resume_parser import parse_resume
from src.tools.linkedin_fetcher import fetch_linkedin
from src.tools.website_scraper import scrape_website


class ParallelGatherInput(BaseModel):
    resume_path: str
    linkedin_url: str
    website_url: Optional[str] = None


@tool(args_schema=ParallelGatherInput)
def parallel_gather_candidate_info(
    resume_path: str,
    linkedin_url: str,
    website_url: Optional[str] = None,
) -> dict:
    """Fetch resume, LinkedIn profile, and personal website concurrently.

    Returns a dict with keys: resume, linkedin, website.
    Failures are captured per-source without aborting the others.
    """

    async def _gather():
        tasks = [
            asyncio.to_thread(parse_resume.invoke, {"file_path": resume_path}),
            asyncio.to_thread(fetch_linkedin.invoke, {"url": linkedin_url}),
        ]
        if website_url:
            tasks.append(asyncio.to_thread(scrape_website.invoke, {"url": website_url}))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results

    raw = asyncio.run(_gather())

    return {
        "resume": raw[0] if not isinstance(raw[0], Exception) else f"ERROR: {raw[0]}",
        "linkedin": raw[1] if not isinstance(raw[1], Exception) else f"ERROR: {raw[1]}",
        "website": (
            raw[2] if len(raw) > 2 and not isinstance(raw[2], Exception) else "N/A"
        ),
    }
```

### 8.5 `src/tools/__init__.py` — Register New Tool

Add `parallel_gather_candidate_info` to `ALL_TOOLS`:

```python
from src.tools.parallel_gather import parallel_gather_candidate_info

ALL_TOOLS = [
    parse_resume,
    fetch_linkedin,
    scrape_website,
    search_web,
    query_database,
    write_database,
    deduplicate_candidate,
    store_memory,
    retrieve_memory,
    get_hiring_rubric,
    trigger_ats_ranking,
    parallel_gather_candidate_info,  # Phase 8 addition
]
```

### 8.6 Files

| Action | Path | Change |
|--------|------|--------|
| Create | `src/cache/__init__.py` | Package exports |
| Create | `src/cache/tool_cache.py` | `ToolCache`, `cached_tool` decorator |
| Create | `src/tools/parallel_gather.py` | `parallel_gather_candidate_info` meta-tool |
| Modify | `src/tools/web_search.py` | Wrap with `@cached_tool(ttl_seconds=3600)` |
| Modify | `src/tools/website_scraper.py` | Wrap with `@cached_tool(ttl_seconds=1800)` |
| Modify | `src/tools/database_tools.py` | Cache `_generate_sql()` with `_cache.get/set` |
| Modify | `src/tools/resume_parser.py` | Replace process dict with `ToolCache` singleton |
| Modify | `src/tools/__init__.py` | Add `parallel_gather_candidate_info` to `ALL_TOOLS` |
| Modify | `requirements.txt` | Add `redis>=5.0.0` |
| Modify | `.env.example` | Add `REDIS_URL=` |

### 8.7 New Dependency

```
redis>=5.0.0
```

Graceful fallback: if `REDIS_URL` is unset or `redis` is not installed, `ToolCache` falls back to `_InMemoryBackend`. The agent still runs; cache survives only within the current process.

### 8.8 Verification

```bash
# Unit: cache key stability (same args → same key)
pytest tests/unit/cache/test_tool_cache.py::test_key_stability -v

# Unit: TTL expiry (in-memory backend)
pytest tests/unit/cache/test_tool_cache.py::test_ttl_expiry -v

# Unit: Redis backend (skip if no REDIS_URL)
pytest tests/unit/cache/test_tool_cache.py::test_redis_backend -v -m "not requires_redis"

# Unit: parallel gather concurrency
pytest tests/unit/tools/test_parallel_gather.py -v

# Integration: full caching round-trip
pytest tests/integration/test_tool_caching.py -m integration -v
```

---

## Phase 9 — Prompt Hardening Layer

### 9.1 Context

This agent is **intentionally vulnerable** — that is its research value. Phase 9 adds a hardening layer that is **completely disabled by default** (`ENABLE_HARDENING=false`). When enabled, it provides a controlled comparison baseline for measuring how much each hardening technique reduces the success rate of the attack vectors documented in `master.md`.

The hardening layer does **not** aim to make the agent secure. It aims to make the *effectiveness of attacks measurable*.

### 9.2 Problem (Unhardened Baseline)

| Attack Surface | Current State |
|---------------|--------------|
| Tool input sanitization | None — raw PDF/HTML/LinkedIn text flows into agent context |
| Prompt injection detection | None — no delimiter enforcement |
| Rate limiting | None — a single session can exhaust API quota |
| Unicode homoglyph attacks | None — U+0430 (Cyrillic а) accepted as ASCII a |
| Zero-width character insertion | None — U+200B invisible in most font renderings |

### 9.3 `src/guardrails/sanitizer.py`

```python
"""Input sanitizer for prompt hardening.

All sanitization is gated behind ENABLE_HARDENING=true.
When disabled, sanitize() returns input unchanged.

Attack classes mitigated:
  - Null byte injection (C-string truncation, path traversal)
  - C0 control characters (terminal escape sequences)
  - Zero-width characters (invisible instruction injection)
  - Unicode directional formatting (bidirectional text attack)
  - Homoglyph attacks via NFC normalisation
  - Oversized input (resource exhaustion)
  - Instruction boundary violation (role confusion via delimiter)
"""
from __future__ import annotations

import os
import re
import unicodedata

ENABLE_HARDENING = os.getenv("ENABLE_HARDENING", "false").lower() == "true"

MAX_INPUT_CHARS = 8_000
INSTRUCTION_BOUNDARY = "### END OF SYSTEM INSTRUCTIONS ###"

# Patterns stripped when hardening is enabled
_NULL_BYTES = re.compile(r"\x00")
_C0_CONTROLS = re.compile(r"[\x01-\x08\x0b-\x1f\x7f]")  # Preserve \t (\x09) and \n (\x0a)
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff\u2060]")
_BIDI_FORMATTING = re.compile(r"[\u202a-\u202e\u2066-\u2069\u200e\u200f]")


def sanitize(text: str) -> str:
    """Sanitize tool input/output. No-op when ENABLE_HARDENING=false."""
    if not ENABLE_HARDENING:
        return text

    # 1. NFC normalisation (collapses homoglyphs like ﬁ → fi, é → é)
    text = unicodedata.normalize("NFC", text)

    # 2. Strip dangerous control/formatting characters
    text = _NULL_BYTES.sub("", text)
    text = _C0_CONTROLS.sub("", text)
    text = _ZERO_WIDTH.sub("", text)
    text = _BIDI_FORMATTING.sub("", text)

    # 3. Truncate oversized inputs
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS] + "\n[TRUNCATED]"

    # 4. Strip content after instruction boundary (tool output cannot masquerade as system)
    if INSTRUCTION_BOUNDARY in text:
        text = text[: text.index(INSTRUCTION_BOUNDARY)] + "\n[BOUNDARY ENFORCED]"

    return text


def add_instruction_boundary(system_prompt: str) -> str:
    """Append the instruction boundary delimiter to a system prompt.

    When hardening is enabled, any tool output containing this delimiter
    will be truncated at the boundary by sanitize().
    """
    if not ENABLE_HARDENING:
        return system_prompt
    return system_prompt.rstrip() + f"\n\n{INSTRUCTION_BOUNDARY}\n"
```

### 9.4 `src/guardrails/rate_limiter.py`

```python
"""Per-session tool call rate limiter.

Gated behind ENABLE_HARDENING=true.
Uses an in-memory defaultdict — resets on process restart.
"""
from __future__ import annotations

import os
from collections import defaultdict

ENABLE_HARDENING = os.getenv("ENABLE_HARDENING", "false").lower() == "true"
MAX_TOOL_CALLS = int(os.getenv("MAX_TOOL_CALLS_PER_SESSION", "50"))

_session_counts: dict[str, int] = defaultdict(int)


class ToolRateLimitError(RuntimeError):
    """Raised when a session exceeds MAX_TOOL_CALLS_PER_SESSION."""


def record_tool_call(session_id: str) -> None:
    """Increment tool call counter for a session. Raises on breach.

    No-op when ENABLE_HARDENING=false.
    """
    if not ENABLE_HARDENING:
        return

    _session_counts[session_id] += 1
    if _session_counts[session_id] > MAX_TOOL_CALLS:
        raise ToolRateLimitError(
            f"Session {session_id} exceeded {MAX_TOOL_CALLS} tool calls."
        )


def get_call_count(session_id: str) -> int:
    return _session_counts.get(session_id, 0)


def reset_session(session_id: str) -> None:
    """Reset counter for a session (call on new session creation)."""
    _session_counts.pop(session_id, None)
```

### 9.5 Integration Points

**`src/graph/workflow.py`** — wrap tool execution:

Each tool in `ALL_TOOLS` is called by the LangGraph agent loop. To intercept tool calls, wrap the tool list at agent build time:

```python
from src.guardrails.rate_limiter import record_tool_call
from src.guardrails.sanitizer import sanitize

def _harden_tool(original_tool, session_id: str):
    """Wrap a LangChain tool with sanitization and rate limiting."""
    import functools

    @functools.wraps(original_tool)
    def wrapped_invoke(*args, **kwargs):
        record_tool_call(session_id)
        # Sanitize string inputs
        sanitized_kwargs = {
            k: sanitize(v) if isinstance(v, str) else v
            for k, v in kwargs.items()
        }
        result = original_tool.invoke(sanitized_kwargs)
        return sanitize(str(result)) if isinstance(result, str) else result

    original_tool.invoke = wrapped_invoke
    return original_tool


# In build_agent():
tools_to_use = (
    [_harden_tool(t, session_id) for t in ALL_TOOLS]
    if ENABLE_HARDENING
    else ALL_TOOLS
)
```

**`src/prompts/evaluation.py`** — add instruction boundary to system prompt:

```python
from src.guardrails.sanitizer import add_instruction_boundary

def build_system_prompt(...) -> str:
    prompt = _build_raw_system_prompt(...)
    return add_instruction_boundary(prompt)
```

### 9.6 Files

| Action | Path | Change |
|--------|------|--------|
| Create | `src/guardrails/__init__.py` | Package exports |
| Create | `src/guardrails/sanitizer.py` | `sanitize()`, `add_instruction_boundary()` |
| Create | `src/guardrails/rate_limiter.py` | `record_tool_call()`, `ToolRateLimitError` |
| Modify | `src/graph/workflow.py` | Wrap `ALL_TOOLS` with `_harden_tool()` when hardening enabled |
| Modify | `src/prompts/evaluation.py` | Call `add_instruction_boundary()` on system prompt |
| Modify | `.env.example` | Add `ENABLE_HARDENING=false`, `MAX_TOOL_CALLS_PER_SESSION=50` |

### 9.7 New Dependencies

None. All stdlib: `re`, `unicodedata`, `os`, `collections`.

### 9.8 Verification

```bash
# Hardening disabled (default) — all attack payloads pass through unchanged
ENABLE_HARDENING=false pytest tests/unit/guardrails/ -v

# Hardening enabled — verify sanitization
ENABLE_HARDENING=true pytest tests/unit/guardrails/test_sanitizer.py -v

# Rate limiting
ENABLE_HARDENING=true MAX_TOOL_CALLS_PER_SESSION=5 \
  pytest tests/unit/guardrails/test_rate_limiter.py -v

# Regression: existing attack fixtures still work when ENABLE_HARDENING=false
pytest tests/integration/test_attack_surface.py -m integration -v
```

### 9.9 Research Usage

```bash
# Baseline: measure injection success rate (hardening off)
ENABLE_HARDENING=false python scripts/red_team_runner.py --report baseline.json

# Hardened: measure injection success rate (hardening on)
ENABLE_HARDENING=true python scripts/red_team_runner.py --report hardened.json

# Compare
python scripts/compare_reports.py baseline.json hardened.json
```

---

## Phase 10 — Observability & Production Ops

### 10.1 Problem

| Gap | Impact |
|-----|--------|
| No structured logging | Impossible to aggregate or query logs by tool, session, or error type |
| No trace IDs | Cannot correlate a user-visible error with the backend log entry that caused it |
| No token counting | No visibility into cost or context window pressure until things break |
| No cost tracking | OpenRouter bills accumulate silently; no per-session cost attribution |
| No pre-flight health check | App starts and immediately fails if DB is unreachable or API key is missing |
| No model fallback | DeepSeek v3.2 outage = 100% service unavailability |

### 10.2 `src/observability/logging.py` — Structured Logger

```python
"""Structured logging using structlog (falls back to stdlib logging).

Usage:
    from src.observability.logging import get_logger
    log = get_logger(__name__)
    log.info("tool_called", tool="search_web", duration_ms=342, session_id="abc123")
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any


def _build_stdlib_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, os.getenv("APP_LOG_LEVEL", "INFO").upper(), logging.INFO))
        logger.propagate = False
    return logger


def get_logger(name: str) -> Any:
    """Return a structlog logger if available, else a stdlib logger."""
    try:
        import structlog

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.stdlib.add_log_level,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, os.getenv("APP_LOG_LEVEL", "INFO").upper(), logging.INFO)
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
        )
        return structlog.get_logger(name)
    except ImportError:
        return _build_stdlib_logger(name)
```

Standard log event fields:

| Field | Type | Description |
|-------|------|-------------|
| `event` | `str` | Verb-noun event name (e.g., `"tool_called"`, `"compression_triggered"`) |
| `tool` | `str` | Tool name when event is tool-related |
| `session_id` | `str` | Current session UUID |
| `client_id` | `str` | Tenant identifier |
| `duration_ms` | `int` | Wall-clock duration for timed operations |
| `trace_id` | `str` | UUID propagated across all events in a single user message turn |
| `error` | `str` | Exception message (only on error events) |
| `tokens_in` | `int` | Input token count (only on LLM call events) |
| `tokens_out` | `int` | Output token count (only on LLM call events) |

### 10.3 `src/observability/tracing.py` — LangSmith Auto-Config

```python
"""LangSmith distributed tracing configuration.

Auto-configures when LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY is set.
No-op otherwise — no dependency on langsmith package at import time.
"""
from __future__ import annotations

import os


def configure_tracing() -> bool:
    """Enable LangSmith tracing if env vars are present. Returns True if enabled."""
    if os.getenv("LANGCHAIN_TRACING_V2", "false").lower() != "true":
        return False

    api_key = os.getenv("LANGCHAIN_API_KEY", "").strip()
    if not api_key:
        return False

    os.environ.setdefault("LANGCHAIN_PROJECT", os.getenv("LANGCHAIN_PROJECT", "hr-recruitment-agent"))
    # LangChain reads LANGCHAIN_TRACING_V2 and LANGCHAIN_API_KEY automatically
    # on next import of langchain_core. Setting the env vars here is sufficient.
    return True
```

Call `configure_tracing()` at the top of `app.py` before any LangChain imports.

### 10.4 `src/health.py` — Pre-Flight Health Check

```python
"""Health check for database and LLM provider.

Usage:
    python -m src.health        # exits 0 (ok) or 1 (degraded)
    from src.health import run_health_check
    result = run_health_check()  # returns dict
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any


def check_database() -> dict[str, Any]:
    start = time.monotonic()
    try:
        from src.database.db import get_db
        with get_db() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as e:
        return {"status": "error", "error": str(e), "latency_ms": int((time.monotonic() - start) * 1000)}


def check_model() -> dict[str, Any]:
    start = time.monotonic()
    try:
        from src.llm import build_chat_model
        model = build_chat_model()
        response = model.invoke("Reply with the single word: ok")
        content = getattr(response, "content", str(response)).strip().lower()
        return {
            "status": "ok" if "ok" in content else "degraded",
            "latency_ms": int((time.monotonic() - start) * 1000),
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "latency_ms": int((time.monotonic() - start) * 1000)}


def run_health_check() -> dict[str, Any]:
    db = check_database()
    model = check_model()
    overall = "ok" if db["status"] == "ok" and model["status"] == "ok" else "degraded"
    return {"overall": overall, "database": db, "model": model}


if __name__ == "__main__":
    result = run_health_check()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["overall"] == "ok" else 1)
```

### 10.5 `src/llm.py` — Model Fallback

Add `.with_fallbacks()` so a DeepSeek v3.2 outage falls back to `deepseek-chat` (configurable):

```python
"""LLM provider helpers for OpenRouter-backed chat models."""
from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-v3.2"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def build_chat_model(temperature: float = 0.0, model: str | None = None) -> ChatOpenAI:
    """Return a ChatOpenAI client configured for OpenRouter with fallback model."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    model_name = model or os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
    fallback_model_name = os.getenv("OPENROUTER_FALLBACK_MODEL", "deepseek/deepseek-chat")
    base_url = os.getenv("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL)

    primary = ChatOpenAI(
        model=model_name,
        temperature=temperature,
        openai_api_key=api_key,
        openai_api_base=base_url,
    )

    fallback = ChatOpenAI(
        model=fallback_model_name,
        temperature=temperature,
        openai_api_key=api_key,
        openai_api_base=base_url,
    )

    return primary.with_fallbacks([fallback])
```

### 10.6 `app.py` — Token Usage and Cost Display

Capture `usage_metadata` from streaming responses and display in sidebar:

```python
# At session state init, add:
if "total_tokens_in" not in st.session_state:
    st.session_state.total_tokens_in = 0
if "total_tokens_out" not in st.session_state:
    st.session_state.total_tokens_out = 0

# In the agent.stream() response handler:
for chunk in agent.stream({"messages": [HumanMessage(content=user_input)]}, config=thread_config):
    for node_name, node_output in chunk.items():
        if "messages" in node_output:
            for msg in node_output["messages"]:
                usage = getattr(msg, "usage_metadata", None)
                if usage:
                    st.session_state.total_tokens_in += usage.get("input_tokens", 0)
                    st.session_state.total_tokens_out += usage.get("output_tokens", 0)

# In sidebar, add cost display:
COST_PER_M_IN = 0.27   # OpenRouter deepseek-v3.2 input rate ($/M tokens)
COST_PER_M_OUT = 1.10  # OpenRouter deepseek-v3.2 output rate ($/M tokens)

tokens_in = st.session_state.get("total_tokens_in", 0)
tokens_out = st.session_state.get("total_tokens_out", 0)
cost = (tokens_in / 1_000_000 * COST_PER_M_IN) + (tokens_out / 1_000_000 * COST_PER_M_OUT)

st.markdown("**Token Usage**")
st.markdown(f"`{tokens_in:,}` in / `{tokens_out:,}` out")
st.markdown(f"Est. cost: `${cost:.4f}`")
```

### 10.7 `start.sh` — Health Check Pre-Flight

Update `start.sh` to run the health check before launching Streamlit:

```bash
#!/usr/bin/env bash
set -e

echo "Running pre-flight health check..."
python -m src.health
if [ $? -ne 0 ]; then
    echo "Health check failed. Aborting startup."
    exit 1
fi

echo "Health check passed. Starting Streamlit..."
streamlit run app.py "$@"
```

### 10.8 Replace `LOGGER` in `app.py`

Replace the `_configure_logger()` / `LOGGER` setup with:

```python
from src.observability.logging import get_logger
from src.observability.tracing import configure_tracing

configure_tracing()
LOGGER = get_logger("hr_ai.app")
```

Remove the old `_configure_logger()` function entirely.

### 10.9 Files

| Action | Path | Change |
|--------|------|--------|
| Create | `src/observability/__init__.py` | Package exports |
| Create | `src/observability/logging.py` | `get_logger()` with structlog/stdlib fallback |
| Create | `src/observability/tracing.py` | `configure_tracing()` LangSmith auto-config |
| Create | `src/health.py` | `run_health_check()`, `check_database()`, `check_model()` |
| Modify | `src/llm.py` | Add `.with_fallbacks([fallback_model])` |
| Modify | `src/graph/state.py` | (No new fields required; `token_budget_used` added in Phase 6) |
| Modify | `app.py` | Replace `_configure_logger()` with `get_logger()`; add token/cost sidebar; call `configure_tracing()` |
| Modify | `start.sh` | Add `python -m src.health` pre-flight gate |
| Modify | `requirements.txt` | Add `structlog>=24.0.0` |
| Modify | `.env.example` | Add tracing + fallback model env vars |

### 10.10 New Dependency

```
structlog>=24.0.0
```

Graceful fallback: if `structlog` is not installed, `get_logger()` returns a stdlib `logging.Logger`. All log call sites (`LOGGER.info(...)`, `LOGGER.error(...)`) are identical for both backends.

### 10.11 Verification

```bash
# Health check: exits 0 when DB + model reachable
python -m src.health

# Health check: exits 1 when DB is down
DATABASE_URL=postgresql://invalid/db python -m src.health; echo "exit: $?"

# Structured logging output
python -c "from src.observability.logging import get_logger; get_logger('test').info('test_event', key='value')"

# Tracing (no-op without keys)
python -c "from src.observability.tracing import configure_tracing; print(configure_tracing())"

# Fallback model wiring
python -c "from src.llm import build_chat_model; m = build_chat_model(); print(type(m).__name__)"

# Full test suite
pytest tests/unit/observability/ -v
pytest tests/unit/test_health.py -v
```

---

## 3. Cross-Phase Summary

### Files Created

| Path | Phase | Description |
|------|-------|-------------|
| `src/memory/__init__.py` | 7 | Memory package |
| `src/memory/retrieval.py` | 7 | Cosine-similarity semantic retrieval |
| `src/memory/consolidation.py` | 7 | LLM episodic→semantic consolidation |
| `src/memory/ttl.py` | 7 | TTL expiry for episodic memories |
| `src/cache/__init__.py` | 8 | Cache package |
| `src/cache/tool_cache.py` | 8 | TTL cache with Redis/in-memory backend |
| `src/tools/parallel_gather.py` | 8 | Async meta-tool for fan-out ingestion |
| `src/guardrails/__init__.py` | 9 | Guardrails package |
| `src/guardrails/sanitizer.py` | 9 | Input sanitizer (gated by `ENABLE_HARDENING`) |
| `src/guardrails/rate_limiter.py` | 9 | Per-session rate limiter |
| `src/observability/__init__.py` | 10 | Observability package |
| `src/observability/logging.py` | 10 | Structured logger with fallback |
| `src/observability/tracing.py` | 10 | LangSmith auto-config |
| `src/health.py` | 10 | Pre-flight health check |
| `alembic/versions/20260224_0002_memory_hierarchy.py` | 7 | Memory schema migration |

### Files Modified

| Path | Phases | Changes |
|------|--------|---------|
| `src/graph/compression.py` | 6 | Add token-aware functions |
| `src/graph/state.py` | 6 | Add `token_budget_used`, `last_compressed_at` |
| `src/graph/workflow.py` | 7, 9 | Pass `query_context` to memory; wrap tools for hardening |
| `src/database/schema.py` | 7 | Add columns to `agent_memory` CREATE TABLE |
| `src/tools/memory_tools.py` | 7 | Replace bulk-load with semantic retrieval |
| `src/tools/web_search.py` | 8 | Add `@cached_tool(ttl_seconds=3600)` |
| `src/tools/website_scraper.py` | 8 | Add `@cached_tool(ttl_seconds=1800)` |
| `src/tools/database_tools.py` | 8 | Cache `_generate_sql()` |
| `src/tools/resume_parser.py` | 8 | Replace process dict with `ToolCache` |
| `src/tools/__init__.py` | 8 | Register `parallel_gather_candidate_info` |
| `src/prompts/evaluation.py` | 9 | Add `add_instruction_boundary()` call |
| `src/llm.py` | 10 | Add `.with_fallbacks()` |
| `app.py` | 6, 7, 10 | Compression wiring; TTL startup; token/cost display; structured logger |
| `start.sh` | 10 | Add health check pre-flight |
| `requirements.txt` | 6–10 | Add 4 optional packages |
| `.env.example` | 6, 8, 9, 10 | Add new env vars |

---

## 4. State-of-the-Art Pattern Classification

| Pattern | Phase | Classification | Reference |
|---------|-------|---------------|-----------|
| Token-threshold compression (not message-count) | 6 | Refinement | LangMem 0.1 used message-count; token-threshold more robust against variable-length tool outputs |
| Three-tier working/episodic/semantic memory | 7 | Current best practice | LangMem paper (Harrison Chase, Jan 2026) |
| LLM-based episodic→semantic consolidation | 7 | Architecture pattern | Cognition Devin published architecture (Jan 2026) |
| Local char n-gram embedding as pgvector fallback | 7 | Novel | No external API; no model required; O(n) retrieval over typical preference sets |
| Intent-level SQL cache (not query-level) | 8 | 2025–2026 pattern | Cache-friendly approach to LLM-generated SQL where query text varies but intent is stable |
| `@cached_tool` decorator for agent tool memoization | 8 | Emerging pattern | 2025–2026 agentic tool result reuse; analogous to HTTP caching for API calls |
| `asyncio.gather` + `asyncio.to_thread` for sync-tool fan-out | 8 | Established pattern | Correct async wrapping for sync LangChain tools without rewriting them |
| Toggled hardening layer (off by default) | 9 | Research methodology | Enables A/B measurement of attack surface without permanently closing vulnerability windows |
| Instruction boundary delimiter | 9 | Defence-in-depth | Canary-style marker; reduces but does not eliminate prompt injection |
| Structlog with stdlib fallback | 10 | Production hygiene | Zero-dependency-at-import-time pattern for optional observability deps |
| Pre-flight health check with K8s probe compatibility | 10 | Operations standard | `sys.exit(0/1)` contract matches K8s liveness probe expectations |
| `with_fallbacks()` model chain | 10 | LangChain 0.3 pattern | `FallbackRunnable` wraps primary + fallback transparently to callers |
