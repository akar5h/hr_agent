# HR Recruitment Agent — agent contributor rules

Project-specific rules. Layers on top of the always-on engineering base in
`~/.claude/CLAUDE.md` (Rule 0 spec-before-code, tests alongside, secrets via env,
minimal surface). This file adds the stack, the domain, and the memory protocol.

A **LangGraph ReAct agent** for multi-tenant candidate sourcing, evaluation, and
ATS ranking. **Intentionally vulnerable by design** — a red-team / security-research
testbed (see the security disclaimer in `docs/trd/hr-recruitment-agent/master.md`).
Never deploy. See §"The testbed rule" below — it overrides the reflex to "fix" bugs.

## Canonical memory — read before work, update after

Two docs are the working memory of this project. Using and maintaining them is part
of every task, not optional overhead:

1. `docs/plan-of-record.md` — **the live work queue + decision log** (deliverable
   index, decisions D1…, task queue). Read it before starting; update task status
   in the same session work lands; append durable decisions with the next D-number.
2. `docs/trd/hr-recruitment-agent/master.md` — architecture + attack-surface map;
   each `phase-*.md` / `plan-*.md` owns its slice (see the deliverable index).

Doc hygiene, every session:
- **Before work:** read `plan-of-record.md` and the doc that owns your task's slice.
  Do not re-derive state from chat history or re-audit what a doc already records.
- **After work lands:** update the plan-of-record status; append new durable decisions
  to its decision log. A finished task whose docs are stale is not finished.
- **Never fork the memory:** no new roadmap/backlog docs. New durable knowledge goes
  into the existing doc that owns it, or a linked reference listed in the deliverable
  index.

## Decision memory per commit (local, never pushed)

Every `git commit` gets an entry in the session's decision-memory file:
`.decision-memory/<YYYY-MM-DD>-<branch>.md` (the directory is gitignored — local-only,
never committed or pushed). The `post-commit` hook auto-scaffolds the stub; fill it:

```markdown
## <commit short-hash> — <subject line>
- Why: the decision(s) behind this change, incl. rejected alternatives
- Evidence: what was verified (tests run, numbers regenerated, docs checked)
- Trace: plan-of-record task ID (P*.*) and any D-number this touches
```

This is the raw journal — intent captured at generation time so review is
verification, not archaeology. When a decision proves durable beyond the session,
**promote it** to the `plan-of-record.md` decision log (next D-number). The local
file is the raw journal; the plan-of-record is the curated memory.

The hook lives versioned in `scripts/git-hooks/`. Fresh clones run once:
`git config core.hooksPath scripts/git-hooks`.

## The testbed rule (non-negotiable — this repo only)

The seeded vulnerabilities ARE the product: prompt injection via resume / LinkedIn
bio / scraped website, cross-tenant data leakage, memory poisoning, rubric/score
manipulation, tool-arg injection. **Do not "fix" a seeded attack vector** — it is the
thing under study, not a bug. When you touch that surface, keep the vuln intact and
say so in the commit + decision memory.

Hardening exists in parallel and is **opt-in behind flags** — it must never silently
close a seeded vector. `ENABLE_HARDENING` gates tenant-boundary + score-creep
enforcement; `ENABLE_PROMPT_CACHE` gates the cached system-prompt path. Side-effect
quotas + audit logging (shortlist / reject / email) are always on. If a change forces
a vector closed by default, that is a scope change — stop and confirm (base Rule 0).

## Hard rules (non-negotiable)

- **No bare `except`.** Catch the specific exception. Audit/best-effort writes swallow
  narrowly and never break the caller — but the swallow is explicit and typed.
- **No silent degradation.** Fail loud. The soft paths are explicit: the fail-soft
  skills loader (warn + skip a malformed file) and best-effort audit writes.
- **Idempotent side effects.** shortlist / reject / send-email derive a stable
  `idempotency_key` and `INSERT ... ON CONFLICT DO NOTHING RETURNING`; replays return
  the prior row with `idempotent_replay=True`. Never double-fire an outreach or a
  decision. (Decision D2.)
- **Tenant scope is bound, not trusted per-query.** Side-effecting tools run inside
  `session_context.session_scope()`; scope DB lookups `WHERE client_id = %s`. (D3.)
- **Skills load, not inferred.** Multi-step recipes go through `load_skill(name)` from
  the frozen registry; the model follows the body verbatim. New recipe → a
  `src/skills/*.md` file, not prose in the prompt. (D4.)
- **Prompt-cache stability.** Keep the stable half of the system prompt stable; only
  client_id / session_id / memories go in the dynamic block. Reordering the cached
  half invalidates every session's cache. (D1.)

## Repo memory (Greplica)

Before broad manual exploration, query repo memory:
`greplica graph context "<question>"` — returns relevant components, flows, decisions,
and code anchors. Near the end of a useful session, save durable decisions / gotchas
with the `greplica-update-working-memory` skill.

## Build & environment

- Python 3.9/3.11. Deps via `pip install -r requirements.txt` (see `requirements.txt`).
- Secrets via `.env` (gitignored); names only in `.env.example`. Keys: OpenRouter,
  Tavily, Langfuse. Never hardcode.
- Run tests before every commit: `pytest tests/`. DB-dependent tests skip without a
  live DB — that is expected; do not mark a change done on skips alone if it touches DB.

## Layout

```
app.py                     Streamlit chat UI (entry point)
server.py                  FastAPI /chat endpoint
src/
  llm.py                   OpenRouter model wiring (Anthropic-haiku primary, DeepSeek fallback)
  database/                db.py, schema.py, seed.py
  tools/                   @tool functions (resume/linkedin/website/db/search/memory)
  graph/                   state.py, workflow.py, ats_subgraph.py, screening_workflow.py
  guardrails/              session_context, sanitizer, audit, rate_limiter, nemo_integration
  memory/                  consolidation, ttl, retrieval
  cache/                   tool_cache (per-turn deterministic tool-result cache)
  skills/                  loader.py, tool.py, *.md recipes
  observability/           tracing, logging, decorators (Langfuse)
  prompts/                 evaluation.py, ats.py
migrations / alembic       schema migrations
tests/                     mirrors src; tests alongside implementation
docs/                      plan-of-record.md + trd/hr-recruitment-agent/*
```

## Conventions

- New `@tool` → `src/tools/`, registered in `ALL_TOOLS`. New skill recipe →
  `src/skills/<name>.md` (filename must match the skill `name`).
- Tests mirror src and land alongside the code, not after.
- Small, focused commits; conventional messages, imperative, why-over-what. Every
  commit fills its decision-memory entry.
