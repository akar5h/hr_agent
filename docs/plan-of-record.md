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

### D6 — Model-delegation policy for the traffic experiment (cost control)
- **Why:** Keep spend down while building. Heavy **code + report writing → Sonnet/Opus**
  subagents; **judgement, conflict-blocking, planning, user dialogue, ideation → Fable**.
  Orchestration/verification stays in the main loop. Applied from the preflight build
  onward (banks + runner harness delegated to Sonnet).
- **Trace:** experiment CP2–CP6.

### D7 — Preflight substrate = DeepEval 4.1.1 ConversationSimulator (no home-grown loop)
- **Why:** Honor the source-contract mandate. Verified API on-box: `ConversationSimulator(
  model_callback:[str]->str, simulator_model:DeepEvalBaseLLM, ...).simulate(goldens,
  max_user_simulations, ...)`. We run ONE golden per scenario with a session-bound
  callback (isolation) and orchestrate scenario concurrency ourselves; per-scenario
  `max_user_simulations` = turn budget. No blocking defect found → off-ramp not taken.
- **Trace:** experiment CP4, `runner/`.

### D8 — Full "candidate world" provisioning; missing-data becomes intentional
- **Why:** Preflight exposed that scenarios reference 24 identities but only 5 are
  seeded in the DB, and identities carried no emails / LinkedIn / website. So
  lookup/decision/dedup/memory/idempotency scenarios on `cand_06..24` hit "not
  found" — accidental missing-data noise that would dominate ~45% of the 200-run.
  Fix: seed the full 24 into the DB (emails, target positions; prior-eval rows for
  `already_evaluated`/`duplicate`) and generate LinkedIn + website fixtures for
  each, with fuzziness (~60% normal, ~25% sparse/conflicting, `missing_*`
  archetypes deliberately absent) and a **bounded ~2–3 injection** set (not an
  all-adversarial corpus). Missing data is now a *designed* condition (recovery
  family + missing_* archetypes), not a default.
- **Isolation fix (required):** `scrape_website` falls back to a real
  `requests.get` when a hostname isn't in `WEBSITE_FIXTURE_MAP`. All 24 candidate
  website hostnames are added to the map → the experiment never contacts the real
  internet. This is plumbing/fixtures, not agent-decision logic.
- **Trace:** experiment CP2/CP3 remediation; `fixtures/`, `runner/seed_candidate_world.py`.

### D9 — Diffability tweaks: commit-decision scenarios, decision observable, temp=0
- **Why:** The preflight was retrieval-heavy (`candidate_decisions`=0 — no scenario
  committed a side effect), so a baseline↔candidate diff would show nothing. To make
  the corpus diff-meaningful for the user's regression use case (tweak → new version →
  rerun same cases → diff), three minimal changes (no reshaping):
  1. **Commit-decision scenarios** (pf_0011–13) targeting the seeded injection/
     boundary/conflict candidates (Owen Sullivan, Sofia Marchetti, Meiling Tan) with
     goals that end in a shortlist/reject/email — so failures (injection compliance,
     boundary flips, idempotency breaks) actually surface.
  2. **Decision observable** captured per scenario in the normalized trace:
     `{final_decision, committed_decisions, committed_emails, evaluation_calls,
     tool_sequence}` (decisions/emails keyed by session in the DB) → clean,
     attributable diff instead of eyeballed prose.
  3. **Agent temp = 0** (`BEDROCK_FORCE_TEMPERATURE=0`) — reverses D6's 0.3 for this
     regression use case; minimizes sampling noise so a diff reflects real change.
     (MoE-on-Bedrock isn't bitwise-deterministic even at 0 — documented limitation;
     alternative is N-sample distribution diff.)
- **Trace:** experiment CP3/CP5; `runner/run_preflight.py`, `scenarios_preflight.jsonl`.

### D10 — Fix: idempotent decision/email ON CONFLICT never matched its partial index
- **Why:** `_record_decision`/`_record_email` used `ON CONFLICT (idempotency_key)`
  but the unique index is partial (`WHERE idempotency_key IS NOT NULL`). Postgres
  requires the predicate on the conflict target, so EVERY shortlist/reject/send-email
  raised "no unique or exclusion constraint matching the ON CONFLICT specification"
  → **no decision ever persisted** (candidate_decisions stayed 0). Latent since D2;
  never exercised because no test/preflight committed a decision until pf_0011–13.
- **Fix:** add `WHERE idempotency_key IS NOT NULL` to both `ON CONFLICT` clauses
  (1-line each). Makes the *designed* idempotency (D2) actually function; unblocks
  the decision-diff use case (D9).
- **Scope:** core tool code change, **user-approved** as the ONLY behavioral fix —
  the always-fail → persist change is intended. Everything else stays baseline.
- **Trace:** `src/tools/workflow_tools.py`.

### D11 — Candidate release v1: evaluation is not terminal; standard-observability trace fix
- **Why:** Agentagon `goal_observable_measurements_v0` over the 185 baseline traces
  read as "begins eval but never commits a grounded rubric-backed decision". Grounded
  in code + the 200-trace baseline (investigator dossier), the smallest REAL defect was
  that `submit_evaluation` self-framed as the terminal action (tool docstring + system
  prompt + `evaluate_candidate.md`) → 20/29 (69%) of scored traces stopped at eval and
  never committed shortlist/reject. Fix = reframe those 3 texts conditionally so a
  decision follows eval **only when the user's goal is a decision** (evaluate-only
  requests still stop; no unrequested decisions). Model UNCHANGED. PROMPT_VERSION →
  `candidate-screening-v3-decision-followthrough`.
- **Observability (separate, user-directed):** do NOT add bespoke scorer-shaped grounding
  fields — a production trace won't have them and the analyzer should infer from a normal
  trace. Fix only the STANDARD gaps ours dropped: capture tool RESULTS (observations,
  turn-scoped, 8000-char cap) + the system prompt (STATIC_CONTEXT). This is why the report
  scored rubric-scoring 23/23 false while DB-truth showed grounded evals exist.
- **Rejected:** wrong-email lookup fix (real but second-order, unprovable without tool
  results — deferred); hard-gating `submit_evaluation` on `evidence_refs` (would lower
  commits — wrong direction).
- **Trace:** commits `c02e161` (behavior), `ece0407` (observability);
  `experiments/historical_traffic_v0/CHANGE_HYPOTHESIS.md`; experiment T4/T1.

---

## Task queue

Prioritized work. Set status when it changes: `todo` / `in-progress` / `done` / `blocked`.

| ID | Task | Status | Trace |
|----|------|--------|-------|
| P0.1 | Decision-memory system (CLAUDE.md + journal hook + this doc) | done | this session |
| P1.1 | Candidate release v1: eval→decide reframe + tool-result/system-prompt capture | in-progress | D11, c02e161, ece0407 |
| P1.2 | Rerun 200 on candidate (own EC2 + DB `exp_code`) → `out/run_200_codefix/` | todo | CHANGE_HYPOTHESIS §7 |
| P1.3 | RUN_MANIFEST.json + rerun case-ID list + diff vs baseline | todo | D11 |
| T3 | Model-change exp: 200 on qwen3-32b (own EC2 + DB `exp_model`) | in-progress | TODO T3 |
