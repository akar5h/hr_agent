# Plan: Agent Hardening + Production Patterns

> **Scope**: Fix known bugs, add memory compression, and bring the agent architecture to 2026 production standards.
> **Priority order**: Bug fixes first → then pattern upgrades → then compression.

---

## 1. Bugs to Fix

### Bug 1 — `force_refresh=True` on every message (app.py:376)

**What it does:**
```python
agent = get_or_create_agent(force_refresh=True)   # ← rebuilds agent every message
```
Every call to `process_user_message` destroys the cached `CompiledGraph` and rebuilds it. The `PostgresSaver` singleton is reused so checkpointed state survives, but:
- Creates a new `CompiledGraph` + binds tools + re-compiles the graph on every message
- Adds ~200–400ms latency per message
- Potential race condition if two messages arrive while rebuild is in progress

**Fix:** Change `force_refresh=True` → `force_refresh=False`. Only force-refresh when the client changes (already handled on line 545) or on explicit user action.

```python
# app.py:376  BEFORE
agent = get_or_create_agent(force_refresh=True)

# AFTER
agent = get_or_create_agent(force_refresh=False)
```

---

### Bug 2 — Agent stops mid-evaluation ("Now let me parse the resume…")

**Root cause:** DeepSeek v3.2 (and similar reasoning models) sometimes emit an intermediate **AIMessage with text content but zero tool_calls** as a "thinking aloud" step before issuing the next tool call. When this message arrives in the stream:

1. `_extract_latest_ai_text` finds it (has text, no tool_calls → qualifies as "final")
2. `full_response` is set to the thinking text
3. The stream loop continues… but if the model stops generating after the thinking text, the loop ends and "Agent completed" fires

This is a **model early-termination bug** amplified by the stream handler treating any tool-call-free AI text as a final answer.

**Evidence:**
```
[01:18:59] tool_call: parse_resume
[01:19:01] tool_result: parse_resume → {"text": "Akarsh Gajbhiye..."}

Now let me parse the resume file to evaluate the candidate:   ← intermediate thinking text
← stream ends here, "Agent completed" shown
```

**Fix A — System prompt: suppress intermediate thinking output**

Add to `build_system_prompt`:
```python
"""
IMPORTANT: Do NOT output intermediate narration like "Now let me..." or "I'll start by...".
Only output text as your FINAL response after all tool calls are complete.
Use tool calls silently. When evaluation is done, output the full result directly.
"""
```

**Fix B — Incomplete-response detection + auto-retry**

In `process_user_message`, after the stream ends, check if the final response looks incomplete. If so, re-invoke with a continuation prompt:

```python
INCOMPLETE_SIGNALS = [
    r"^now let me\b",
    r"^let me\b",
    r"^i (will|'ll|need to|should) (now |next |then )?\b(parse|fetch|scrape|search|evaluate|score)\b",
    r"^based on the (rubric|resume|profile),? i (will|'ll|now)\b",
    r":$",          # ends with colon — clearly a preamble
]

def _looks_incomplete(text: str) -> bool:
    t = text.strip().lower()
    if len(t) < 150:      # suspiciously short for a full evaluation
        for pattern in INCOMPLETE_SIGNALS:
            if re.search(pattern, t, re.IGNORECASE | re.MULTILINE):
                return True
    return False
```

If `_looks_incomplete(full_response)` after the stream: append a `HumanMessage("Continue the evaluation — provide the full scoring and recommendation.")` and re-invoke once.

**Fix C — Add `recursion_limit` guard to `create_react_agent`**

Prevent infinite loops while allowing complex multi-step evaluations:
```python
agent = create_react_agent(
    model=model,
    tools=AGENT_TOOLS,
    prompt=system_prompt,
    checkpointer=checkpointer,
)
# Set recursion limit on each invocation config:
config = {
    "configurable": {"thread_id": session_id},
    "recursion_limit": 25,   # max 25 agent steps
}
```

---

### Bug 3 — PostgreSQL query uses `%s` placeholders for SQLite syntax

In `trigger_ats_ranking` (workflow.py:47), the query uses `ILIKE` and multiple `%s` which is valid PostgreSQL but breaks if running against SQLite in tests:
```sql
OR p.title ILIKE %s   -- PostgreSQL only
```
The DB layer was changed from SQLite → Postgres (db.py) so this is now correct for prod, but all unit tests that mock `get_db()` with a SQLite connection will fail. **Fix:** Ensure test fixtures use psycopg-compatible connections or mock the DB layer at a higher level.

---

### Bug 4 — `create_agent` import may not exist

`workflow.py:8`: `from langchain.agents import create_agent`

In LangChain ≥ 0.1 / LangGraph ≥ 0.3, the correct import is:
```python
from langgraph.prebuilt import create_react_agent
```

`langchain.agents.create_agent` is not a documented public API. If it works today, it may be an internal alias that breaks on the next release. **Fix:** Replace the import and usage:

```python
# BEFORE (workflow.py)
from langchain.agents import create_agent
...
return create_agent(model=model, tools=AGENT_TOOLS, system_prompt=system_prompt, checkpointer=checkpointer)

# AFTER
from langgraph.prebuilt import create_react_agent
...
return create_react_agent(model=model, tools=AGENT_TOOLS, prompt=system_prompt, checkpointer=checkpointer)
```

---

## 2. Memory Architecture Upgrade

### Current state
- `store_memory` / `retrieve_memory` are passive tools — the agent must be explicitly prompted to use them
- No automatic session start retrieval
- No TTL or scoping beyond `session_id + client_id`
- Memory grows unbounded (no eviction)
- `MemorySaver` (from TRD) was replaced with `PostgresSaver` (good) — this is persistent across restarts

### Target: Structured Persistent Memory with Auto-Retrieval

**2026 production pattern**: Three memory tiers used by real agentic systems (Cognition AI Devin, LangChain production deployments, Cohere's agent orchestrator):

| Tier | What | TTL | Where |
|------|------|-----|-------|
| **Working memory** | Current conversation + tool results | Session | LangGraph `messages` (PostgresSaver) |
| **Episodic memory** | Scored candidate summaries, previous evaluations | 30 days | `agent_memory` table, key=`eval_summary:{candidate_id}` |
| **Semantic memory** | Cross-session patterns, client preferences learned | Permanent | `agent_memory` table, key=`client_pref:{client_id}` |

**Implementation changes:**

**a) Auto-inject episodic memory at session start**

Add to `build_system_prompt` — append retrieved memories:
```python
def build_system_prompt(client_id: str, session_id: str, prior_memories: list[dict] | None = None) -> str:
    base = f"""..."""  # existing prompt
    if prior_memories:
        memory_block = "\n".join(f"- {m['memory_key']}: {m['memory_value']}" for m in prior_memories)
        base += f"\n\nRelevant context from previous sessions:\n{memory_block}"
    return base
```

Fetch in `build_agent()`:
```python
def build_agent(client_id: str, session_id: str):
    # Load recent episodic memories for this client
    from src.tools.memory_tools import _load_client_memories
    prior_memories = _load_client_memories(client_id=client_id, limit=10)
    system_prompt = build_system_prompt(client_id, session_id, prior_memories=prior_memories)
    ...
```

**b) Add `_load_client_memories` helper** (not a tool — internal function)
```python
def _load_client_memories(client_id: str, limit: int = 10) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT memory_key, memory_value FROM agent_memory "
            "WHERE client_id = %s AND memory_key LIKE 'client_pref:%' "
            "ORDER BY created_at DESC LIMIT %s",
            (client_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]
```

**c) Auto-summarize evaluation results into episodic memory**

After a successful evaluation write, the agent should call:
```python
store_memory(
    session_id=session_id,
    client_id=client_id,
    memory_key=f"eval_summary:{candidate_id}",
    memory_value=f"Score: {overall_score}/10 | Recommendation: {recommendation} | {one_line_summary}"
)
```

Enforce this by adding a line to the system prompt:
```
After every completed candidate evaluation, call store_memory with:
  memory_key = "eval_summary:{candidate_id}"
  memory_value = one-sentence summary with score and recommendation
```

---

## 3. Context Window Compression (MANDATORY ADD)

### Problem
Tool results accumulate in `messages`:
- `parse_resume` → can return 3,000+ tokens (full resume text)
- `fetch_linkedin` → 500–800 tokens
- `scrape_website` → 1,000–2,000 tokens
- `search_web` → 2,000–5,000 tokens (5 results)

For a 5-candidate session, context can exceed 40,000 tokens before the evaluation even starts. DeepSeek v3.2's context window is 64K tokens — this is a real limit.

### Solution: Tool Result Truncation + Message Summarization

**Step 1: Truncate tool outputs at source**

Add a `MAX_TOOL_OUTPUT_CHARS` limit to all text-heavy tools:

```python
# src/tools/resume_parser.py
MAX_RESUME_CHARS = 4000   # ~1000 tokens

@tool(args_schema=ParseResumeInput)
def parse_resume(file_path: str) -> dict:
    ...
    text = extracted_text
    truncated = False
    if len(text) > MAX_RESUME_CHARS:
        text = text[:MAX_RESUME_CHARS] + "\n[... truncated for context efficiency ...]"
        truncated = True
    return {"text": text, "hash": file_hash, "pages": page_count, "truncated": truncated}
```

| Tool | Max output | Rationale |
|------|-----------|-----------|
| `parse_resume` | 4,000 chars | Key skills + experience sections are in first 4K |
| `scrape_website` | 2,000 chars | About page typically < 2K |
| `search_web` | 500 chars/result | Snippet is enough for candidate research |
| `query_database` | 50 rows max | Prevent bulk dumps |
| `retrieve_memory` | 20 entries max | Oldest entries pruned |

**Step 2: ToolMessage summarization in the streaming loop**

After receiving a tool result, summarize if it exceeds a threshold:

```python
# In app.py process_user_message — after tool result received
SUMMARIZE_THRESHOLD = 800  # chars

def _maybe_summarize_tool_result(tool_name: str, content: str) -> str:
    """Return content as-is if small; return a compact summary if large."""
    if len(content) <= SUMMARIZE_THRESHOLD:
        return content
    # Only summarize for known long-output tools
    if tool_name in ("parse_resume", "scrape_website", "search_web"):
        return content[:SUMMARIZE_THRESHOLD] + f"\n[+{len(content) - SUMMARIZE_THRESHOLD} chars truncated]"
    return content
```

**Step 3: Sliding window message compression (the real production pattern)**

When `messages` list exceeds N tokens, compress older tool call/result pairs into a summary:

```python
# src/graph/compression.py  (new file)

MAX_MESSAGES_BEFORE_COMPRESS = 20   # after 20 messages, compress older ones
KEEP_RECENT_MESSAGES = 8            # always keep last 8 messages intact

def compress_messages(messages: list, model) -> list:
    """
    Summarize messages[:-KEEP_RECENT_MESSAGES] into a single SystemMessage summary.
    Keeps the last N messages and the original system message intact.

    Pattern used by: Cognition Devin, LangMem, LangChain summarize_chat_history
    """
    if len(messages) <= MAX_MESSAGES_BEFORE_COMPRESS:
        return messages

    system_messages = [m for m in messages if getattr(m, "type", "") == "system"]
    to_compress = messages[len(system_messages):-(KEEP_RECENT_MESSAGES)]
    keep = messages[-(KEEP_RECENT_MESSAGES):]

    if not to_compress:
        return messages

    # Build a compression prompt
    history_text = "\n\n".join(
        f"[{getattr(m, 'type', 'msg').upper()}]: {getattr(m, 'content', '')[:500]}"
        for m in to_compress
    )
    summary_prompt = f"""Summarize the following agent conversation history in 3-5 sentences.
Preserve: candidate names evaluated, scores given, rubric used, key findings.
Omit: raw tool output text, repeated reasoning, intermediate steps.

History:
{history_text}"""

    summary_response = model.invoke(summary_prompt)
    summary_text = getattr(summary_response, "content", str(summary_response))

    from langchain_core.messages import SystemMessage
    summary_msg = SystemMessage(content=f"[Compressed history]: {summary_text}")

    return system_messages + [summary_msg] + keep
```

Wire this into the agent invocation:
```python
# In workflow.py build_agent — add a pre-processing node or call before invoke
# Simplest approach: compress in process_user_message before passing to agent
```

**Step 4: LangMem integration (optional, modern)**

`langmem` (LangChain's memory library, released Jan 2026) provides production-ready memory management:
```python
pip install langmem

from langmem import create_memory_store, MemoryManager

# Auto-extracts important facts from conversation and stores them
memory_store = create_memory_store(...)
manager = MemoryManager(model=model, store=memory_store)
```
Evaluate adopting LangMem in a future phase.

---

## 4. Additional Production Patterns

### 4.1 Tool Result Caching (Idempotency)

Same resume file parsed twice in same session wastes 2 LLM + parse calls. Cache by content hash:

```python
# In parse_resume tool
RESULT_CACHE: dict[str, dict] = {}   # module-level cache, bounded

def parse_resume(file_path: str) -> dict:
    file_hash = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
    if file_hash in RESULT_CACHE:
        return {**RESULT_CACHE[file_hash], "cached": True}
    result = _do_parse(file_path)
    RESULT_CACHE[file_hash] = result
    return result
```

### 4.2 Max Iterations Guard

Without a step limit, a confused agent can loop indefinitely on tool calls. Set via `recursion_limit`:
```python
# In process_user_message (app.py)
config = {
    "configurable": {"thread_id": st.session_state.session_id},
    "recursion_limit": 25,
}
for chunk in agent.stream({"messages": [HumanMessage(content=user_input)]}, config=config, ...):
```

### 4.3 Structured Output for Final Evaluation

Instead of free-text evaluation, add a `submit_evaluation` tool with a strict Pydantic schema. This prevents "almost done" responses and forces the agent to produce machine-readable output:

```python
class EvaluationResult(BaseModel):
    candidate_name: str
    position_id: str
    technical_score: float        # 0-10
    experience_score: float       # 0-10
    culture_score: float          # 0-10
    communication_score: float    # 0-10
    overall_score: float          # weighted by rubric
    recommendation: Literal["STRONG_HIRE", "HIRE", "CONSIDER", "PASS"]
    reasoning: str                # 2-3 sentences

@tool(args_schema=EvaluationResult)
def submit_evaluation(candidate_name: str, position_id: str, ...) -> dict:
    """Submit the final structured evaluation. MUST be called to complete evaluation."""
    # Writes to DB, returns confirmation
    ...
```

Add to system prompt: `"Always call submit_evaluation as your final step. Do not output evaluation text without calling this tool."`

This pattern **eliminates the mid-evaluation stop bug** at the architectural level — the agent loop only terminates after `submit_evaluation` is called.

### 4.4 Streaming: Emit Tool Progress to UI

Currently the status widget shows tool names but not progress within a tool. For long-running tools (web_search, scrape_website), add a yield-based progress update:

```python
# In app.py streaming loop — show estimated progress
TOOL_DURATIONS = {
    "parse_resume": "~2s",
    "scrape_website": "~5s",
    "search_web": "~3s",
    "fetch_linkedin": "~1s",
}
if msg_type == "ai" and tool_call_names:
    duration_hints = ", ".join(
        f"{n} ({TOOL_DURATIONS.get(n, '...')})" for n in tool_call_names
    )
    log_line = _append_agent_log(f"tool_call: {duration_hints}")
```

### 4.5 Model Fallback

DeepSeek v3.2 via OpenRouter can hit rate limits or return 5xx. Add a fallback:

```python
# src/llm.py
FALLBACK_MODEL = "anthropic/claude-3.5-haiku"

def build_chat_model(...) -> ChatOpenAI:
    primary = ChatOpenAI(model=model_name, ...)
    fallback = ChatOpenAI(model=FALLBACK_MODEL, ...)
    return primary.with_fallbacks([fallback])
```

LangChain's `.with_fallbacks()` transparently switches to the fallback on errors.

### 4.6 Input Validation on Tools

Current tools accept raw strings with no validation. Add at minimum:

```python
# In scrape_website
from urllib.parse import urlparse

def _validate_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)

@tool(args_schema=ScrapeWebsiteInput)
def scrape_website(url: str) -> dict:
    if not _validate_url(url):
        return {"error": f"Invalid URL: {url}"}
    ...
```

---

## 5. Implementation Order

| Step | Change | Effort | Impact |
|------|--------|--------|--------|
| 1 | Fix `force_refresh=True` → `False` in app.py:376 | 1 line | Medium |
| 2 | Add `submit_evaluation` structured tool + system prompt instruction | 1 day | **Eliminates mid-stop bug** |
| 3 | Fix `create_agent` → `create_react_agent` import | 10 min | High (future-proofing) |
| 4 | Add `recursion_limit: 25` to all agent invocations | 2 lines | Medium |
| 5 | Tool output truncation (all text-heavy tools) | 0.5 days | High (prevents OOM) |
| 6 | Message compression in `src/graph/compression.py` | 1 day | **Core context management** |
| 7 | Auto-inject client memories into system prompt | 0.5 days | High (persistent memory) |
| 8 | Tool result caching by hash | 0.5 days | Medium |
| 9 | Incomplete-response detection + retry | 0.5 days | Medium (safety net) |
| 10 | Model fallback with `.with_fallbacks()` | 1 hour | Medium |

---

## 6. Verification

```bash
# Bug 1: force_refresh fix
grep -n "force_refresh" app.py   # should show False

# Bug 2: submit_evaluation exists
python -c "from src.tools.database_tools import submit_evaluation; print(submit_evaluation)"

# Bug 3: create_react_agent
python -c "from src.graph.workflow import build_agent; a = build_agent(); print(type(a).__name__)"
# Expected: CompiledStateGraph

# Context compression
python -c "from src.graph.compression import compress_messages; print('ok')"

# Memory injection
python -c "from src.prompts.evaluation import build_system_prompt; p = build_system_prompt('c', 's', [{'memory_key':'k','memory_value':'v'}]); assert 'context from previous' in p"

# Full integration
python -m pytest tests/ -x --tb=short
```
