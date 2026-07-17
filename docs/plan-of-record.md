# Plan of Record — HR Recruitment Agent

The **live work queue + decision log**. Check it before starting work; update the
status column when work lands; append durable decisions to the decision log with
the next D-number. A finished task whose entry here is stale is not finished.

This is the *curated* memory. The raw journal is `.decision-memory/<date>-<branch>.md`
(local-only, per commit). Promote decisions that outlive their session up to the
decision log below.

---

## Deliverable index

| Doc | Owns |
|-----|------|
| `docs/trd/hr-recruitment-agent/master.md` | Overview, architecture, attack-surface map |
| `docs/trd/hr-recruitment-agent/phase-1-database.md` | SQLite schema, db.py, seed |
| `docs/trd/hr-recruitment-agent/phase-2-tools.md` | The `@tool` functions + vuln callouts |
| `docs/trd/hr-recruitment-agent/phase-3-langgraph.md` | ReAct agent, MemorySaver, system prompt |
| `docs/trd/hr-recruitment-agent/phase-4-ats-subgraph.md` | ATS sub-agent, `trigger_ats_ranking` |
| `docs/trd/hr-recruitment-agent/phase-5-streamlit-ui.md` | `app.py` chat UI |
| `docs/trd/hr-recruitment-agent/phase-6-10-production-hardening.md` | Hardening roadmap |
| `docs/trd/hr-recruitment-agent/plan-hardening.md` | Phase-A reliability sweep design |
| `docs/trd/hr-recruitment-agent/plan-skills-loader.md` | Skills loader design + architecture |
| `docs/trd/hr-recruitment-agent/plan-attack-suite.md` | Red-team attack suite |
| `docs/reliability-diagnostic-action-plan.md` | Reliability diagnostics |

---

## Decision log

Durable decisions, newest-appended. Format: `D<n> — <title>` · Why · Rejected · Trace.

### D1 — OpenRouter Anthropic-Haiku primary, DeepSeek fallback, cache_control-capable
- **Why:** System prompt needs Anthropic-style `cache_control` blocks for prompt
  caching. `anthropic/claude-3.5-haiku` primary supports it; `deepseek/deepseek-v3.2`
  is the fallback. `build_system_prompt` split into a stable cached block + a small
  dynamic block (client_id / session_id / memories) so cache hits do not drift.
- **Rejected:** DeepSeek-only (no `cache_control`, no caching).
- **Trace:** commit `89ec601`. Gated by `ENABLE_PROMPT_CACHE`.

### D2 — Idempotent side-effecting tools via stable idempotency_key
- **Why:** shortlist / reject / email replays must not double-fire.
  `idempotency_key = (action, session_id, candidate_id, position_id)` (or subject
  hash for email); `INSERT ... ON CONFLICT DO NOTHING RETURNING`; replays return the
  prior row with `idempotent_replay=True`. Migration `20260514_0004` adds the columns
  + unique partial indexes.
- **Trace:** commit `89ec601`.

### D3 — Tenant boundary is a bound session scope, not per-query trust
- **Why:** `session_context.session_scope()` binds `(client_id, session_id)` for a
  tool invocation. `write_database` refuses any mutation whose `client_id` disagrees;
  workflow tools scope lookups `WHERE client_id = %s` so a cross-tenant `candidate_id`
  resolves to "not found for this client". Per-tool quotas + an `audit_events` table
  (best-effort, never breaks the caller) back it.
- **Note:** This hardening is *opt-in* — the seeded cross-tenant vulnerabilities are
  the product (see CLAUDE.md hard rule). Enforcement is gated by `ENABLE_HARDENING`;
  side-effect quotas are always on.
- **Trace:** commits `89ec601`, `7f2b4c8` (score-creep + SQL-identifier block).

### D4 — Skills = pre-written tool-call DAGs loaded on demand, not inferred per turn
- **Why:** Multi-step recipes (evaluate / decide / outreach / rank / recall) load via
  `load_skill(name)` from a frozen, module-cached registry, and the model follows the
  body verbatim. The skills index lives inside the *cached* half of the system prompt
  so adding/removing a skill invalidates the cache once, then is stable. Loader is
  fail-soft (warn + skip malformed files, never break the registry).
- **Rejected:** letting the LLM re-infer the recipe every turn (nondeterministic,
  no cache locality).
- **Trace:** commits `6171aa4` (loader + 5 skills), `3366c59` (architecture + diagrams
  + priority-tagged risks in `plan-skills-loader.md`).

### D5 — Agent LLM migrated OpenRouter → AWS Bedrock; Qwen3-235B agent (DeepSeek dropped)
- **Why:** The historical-traffic experiment runs end-to-end on AWS (Bedrock + RDS +
  SSM-reachable EC2). `build_chat_model` (single chokepoint) rewired to
  `ChatBedrockConverse`. Per-level model override kept: agent + ATS + screening =
  `qwen.qwen3-235b-a22b-2507-v1:0`, SQL-gen = `qwen.qwen3-32b-v1:0`, throttle fallback
  = `moonshotai.kimi-k2.5`. Agent temp = 0.3 (`BEDROCK_FORCE_TEMPERATURE`), realistic
  low temp per doubt-review D6.
- **Rejected:** DeepSeek-V3.2 as agent — verified on box it leaks DeepSeek `<｜DSML｜>`
  function-call markup into the text channel AND fails to terminate in the ReAct loop
  (hit the 50-step recursion limit). Qwen3-235B answered cleanly and terminated.
- **Evidence:** on-box smoke — correct techcorp-positions answer, 10,103 in / 106 out
  tokens, Langfuse trace landed (`us.cloud.langfuse.com`). Migrations+seed on new RDS:
  2 clients / 5 positions / 5 rubrics / 5 candidates.
- **Note:** prompt caching OFF (open-weight cache support unclear); ~10k input
  tokens/turn from the system prompt is the main cost driver — quantified by the
  10-scenario preflight (CP6). This is plumbing (provider swap), agent decisions
  unchanged (core frozen).
- **Trace:** experiment `experiments/historical_traffic_v0/` CP0.

---

## Task queue

Prioritized work. Set status when it changes: `todo` / `in-progress` / `done` / `blocked`.

| ID | Task | Status | Trace |
|----|------|--------|-------|
| P0.1 | Decision-memory system (CLAUDE.md + journal hook + this doc) | done | this session |
| _add next tasks here_ | | | |
