# EXPERIMENT_PLAN — HR AI Historical-Traffic Generator (v0)

Disposable external test infra that drives the **unchanged** hr_ai agent through
200 pre-frozen conversation scenarios and captures an enriched, provider-neutral
trace corpus for a separate downstream experiment (Agentagon Seed V0). This repo
does NOT grade, compare, mine seeds, or build product UI.

Source task: `/Users/akarshgajbhiye/agentagon/experiments/hr_ai_traffic_generation/CLAUDE_IMPLEMENTATION_PROMPT.md`

> **Design was adversarially reviewed (doubt-driven, single-model).** 16 findings
> folded in. The central correction: the corpus's goals (reads-human, distinct,
> not-collapsed) are **output** properties — so this plan **measures diversity on
> realized traces and gates on it**, rather than assuming input-side coverage
> knobs produce it. See §11 (diversity gates) and §12 (honesty/provenance).

## 0. Core vs plumbing (the invariant)

**Plumbing — may change** (does not alter the agent's decisions):
- LLM provider: OpenRouter → **AWS Bedrock** (`build_chat_model` single chokepoint).
- DB: Neon → **AWS RDS Postgres** (env `DATABASE_URL` only).
- Trace backend: **Langfuse cloud** (new keys) + enriched span/reasoning capture.
- Prompt-caching flag, OpenTelemetry/span enrichment, session plumbing, agent temp.

**Core — frozen** (baseline stays behaviorally identical):
- Agent system-prompt text, tool logic, graph shape, ATS sub-agent, scoring,
  business decisions. No edits while generating the baseline.

Every plumbing change to the hr_ai core repo gets a `.decision-memory/` entry and
a plan-of-record D-number (see hr_ai `CLAUDE.md`).

## 1. Runtime architecture

- **In-process import.** Harness drives `build_agent(...).stream(...)` directly
  (mirrors `server._run_turn`): full access to every AIMessage tool_call +
  ToolMessage result + Langfuse callback. Richer than the shallow HTTP list.
- **DeepEval `ConversationSimulator`** owns turn orchestration.
  `model_callback(input, turns, thread_id)` → in-process runner; `thread_id` ==
  hr_ai `session_id`. `simulator_model` = custom `DeepEvalBaseLLM` (Bedrock).
  `stopping_controller` + `simulation_graph` bound behavior.

## 2. Models (Bedrock; region chosen for availability+quota, not just price — §CP0)

| Level | Model (verified in ap-south-1) | Notes |
|---|---|---|
| Main ReAct agent | **Qwen3-235B** (`qwen.qwen3-235b-a22b-2507-v1:0`) | clean tool-use, terminates. **DeepSeek-V3.2 DROPPED**: DSML function-call leak into text + ReAct non-termination (hit 50-recursion loop). low nonzero temp (0.3) |
| ATS + screening | Qwen3-235B | same chokepoint |
| SQL-generator tool | Qwen3-32B (`qwen.qwen3-32b-v1:0`) | cheapest, narrow task |
| Agent fallback (throttle) | Kimi-K2.5 (`moonshotai.kimi-k2.5`) | — |
| DeepEval simulator | **rotation: Kimi-K2.5 + GLM-4.7** (`moonshotai.kimi-k2.5`, `zai.glm-4.7`), **decorrelated from the Qwen agent** | breaks weight-level monoculture; register-monoculture is a *documented limitation* (§12), not assumed solved |

Wired via env in `src/llm.py` (`build_chat_model` → `ChatBedrockConverse`), per-call
`model=` override preserved. **Pin the concrete Bedrock model-version string from
each response's metadata** (not the alias) into every trace (§12). Agent temp is a
small realistic nonzero with a logged seed — MoE-on-Bedrock is not bitwise
deterministic at temp=0, so temp=0 buys neither reproducibility nor variety; a
logged low temp gives agent-side behavioral signal.

`ENABLE_PROMPT_CACHE=false` for the agent (open-weight cache support unclear;
cleaner enriched baseline).

## 3. Frozen environment (pinned in RUN_MANIFEST)

`ENABLE_HARDENING=false`, `ENABLE_NEMO_GUARDRAILS=false` (true passthrough — no
tokens/latency), **memory ENABLED + hardened** (§4), `MAX_TOOL_CALLS_PER_TURN=25`,
`ENABLE_LANGFUSE=true`. Record: model IDs + version hashes, prompt/tool/agent
versions, repo SHA, dep lock hash, DeepEval version, region, scenario-manifest hash,
agent temp + seed.

## 4. Memory: enabled + hardened before freeze (NOT paused)

The memory subsystem stays ON — recurrence, dedup, cross-session memory, and
idempotency are core surfaces the corpus must exercise. **Harden it in CP0 before
the manifest freeze** so its behavior is fixed for the whole run. CP0 also runs a
**keying probe** (§5) — memory/dedup may key on content (fuzzy name / embedding),
not just synthetic ID, which changes the isolation story.

## 5. State isolation — controlled sharing, not blanket (single shared RDS DB)

Blanket ID-isolation would *destroy* the recurrence/dedup/memory/idempotency signal
(the agent would never see a repeat). So:

- **Sharing groups (deliberate):** dedup, recurrence, cross-session memory,
  idempotency families reuse the SAME candidate identities in a **fixed order**,
  run **sequentially** within the group. This is the point — the agent must see
  the repeat.
- **Independent scenarios:** disjoint synthetic-ID keyspace + fresh session, run
  **concurrently**.
- **Keying probe (CP0, blocking):** empirically determine what memory/dedup key on.
  If **content-keyed**, disjoint IDs are insufficient — same-candidate independent
  scenarios must not run concurrently, or must be namespace/tenant-partitioned.
- **Contamination canary:** a probe scenario that can only "see" another scenario's
  write if isolation leaked; run it under concurrency and assert clean.
- State-before/after refs captured for every mutating scenario. Reseed/verify only
  at phase boundaries. **Order-sensitivity:** run ≥2 alternate orderings for a
  subset of memory/dedup groups (single frozen order hides order-dependent bugs).

## 6. Frozen traffic design

- 200 scenarios, pre-registered + hashed before any run. No silent add/remove/relabel.
- 160 chat-led / 40 upload-backed.
- Depth (user turns): 100×(2-3), 70×(4-6), 30×(7-8), max 8, no 1-turn — but
  **conversation shape drives the length budget** (spiral/escalate/abandon need
  room); realized shape is verified against labeled shape (§11), not trusted.
- 24 candidate identities (5 seeded + 19 synthetic) reused across scenarios.
- 24 recruiter identities / 8 archetypes / 3 each, differing in ≥4 behavioral
  properties (measured on output, §11). Archetype 8 = adversarial/boundary.
- **Family quotas — DEVIATION from source (approved):** upweight hard families for
  the downstream dirty-trace goal. Shift ~15–20 scenarios out of eval/lookup into
  dedup/idempotency, memory, recovery, and security. Logged as an intentional
  manifest deviation with rationale. Still 200 total, 80/20 preserved.
- Uniqueness gate on **both** user-turn text (exact + embedding near-dup) **and
  realized trajectory** (tool-call sequence + DB deltas). Near-dups reported, not
  deleted.

## 7. TurnGenerator + split validator

1. Deterministic scenario controller owns immutable case truth (identities, goal,
   known/hidden facts, disclosure order, corrections, turn budget, stop conditions).
2. Rotated Bedrock simulator converts truth + visible convo → next English user
   message, constrained to one pre-authorized reaction; strict JSON out.
3. **Two-tier validator (the key doubt-driven fix):**
   - **HARD-reject** only: leakage of hidden ground-truth labels, and violations of
     the scenario's OWN fixed facts, unauthorized tool instructions.
   - **ALLOW (do not sanitize):** typos, terseness, rudeness, impatience, AND
     **recruiter false beliefs about the candidate** ("wasn't she at Google?" — she
     wasn't). False beliefs are the corpus's most valuable human content.
   - Reject → retry once → second failure = `SIMULATOR_ERROR` (not an agent
     failure). **Log every first-attempt rejection + reason**; audit whether
     rejection rate correlates with family/difficulty (retry can shape outcomes).
- Simulator I/O / hashes / retries / latency / tokens / cost stored SEPARATELY from
  the agent trace. Low nonzero temp for wording only; semantics deterministic.

## 8. Trace capture (enriched)

Provider-native Langfuse spans retained. Plus one provider-neutral JSONL record per
scenario (envelope per source spec). Capture ordered user/agent/tool/workflow/system
events, tool name+normalized args+status+timing+result ref+error, **reasoning/
thinking tokens** (labeled opaque provenance, §12), upload artifact hash (not raw
bytes), all versions+identities, budget/timeout/retry/infra errors, token+cost,
state-before/after for mutating cases. Execution-gap taxonomy: `COMPLETED | TIMEOUT
| HARNESS_ERROR | ENVIRONMENT_ERROR | BUDGET_EXHAUSTED`. Failed agent interaction =
valid COMPLETED trace; untraceable run = distinguishable gap.

## 9. Concurrency, backoff, DLQ, budget

- Bounded concurrency sized to **actual in-region TPM quota** (verified CP0), not a
  guessed number.
- Bedrock throttling → exponential backoff + retry (tagged infra, not agent).
- Terminal failures → **dead-letter queue** (`out/*/dead_letter.jsonl`), reprocessable.
- **Hard LLM budget $45.** Full cost model BEFORE freeze (real per-call token counts
  incl. reasoning + validator retries + the 30 long scenarios). **Randomize
  execution order** + **stratified per-family budget reservation** so a shortfall
  never silently zeroes a family (the tail = long/security/spiral = the expensive,
  non-random cases). If the model says >$45, cut scenario count honestly up front —
  never truncate mid-run. Preserve completed artifacts on stop; never hide overruns.

## 10. Execution phases → TODO.md checkpoints (CP0–CP9).

## 11. Output-side diversity gates (pre-registered, the real acceptance test)

Input coverage ≠ output diversity. Pre-register and publish these on the realized
corpus; they gate acceptance:
- **Effective sample size:** embed full traces → cluster at fixed threshold (or
  Vendi score). If effective-N ≪ 200, the corpus is smaller than it looks — fail.
- **Tool-sequence n-gram entropy** across traces.
- **Trajectory realized-vs-labeled shape** agreement.
- **Persona archetype recovery:** classifier recovers archetype from output text;
  low accuracy = personas collapsed to base voice regardless of input spec.
- **Real-vs-synthetic discriminator spot-check** (rater or classifier) as a
  reads-human gate; report accuracy. High rater accuracy = Contract-2 failure,
  honestly reported.
- **Injected-dirtiness distribution check:** injected typos/errors must be
  distributionally like real dirtiness (clustered on domain terms/fatigue), not a
  flat rate; verify no verbatim n-grams leak from the seed corpora.

## 12. Honesty & provenance controls

- **Run provenance:** log EVERY run attempt with a monotonic counter (incl.
  discarded). Pre-declare the keep-rule. Pin concrete Bedrock model-version hashes.
  "Frozen manifest" covers inputs; this covers the run + analysis.
- **Reasoning tokens = opaque provenance metadata.** DeepSeek/Qwen CoT is
  known-unfaithful; document it; the downstream experiment must NOT treat reasoning
  tokens as ground-truth cognition.
- **No post-hoc relabeling.** Preserve surprising/failed outcomes as valid traces.
  Keep agent vs simulator vs infra attribution clean — do NOT launder hard agent
  cases into `SIMULATOR_ERROR`.

## 13. Outputs (under this dir)

```
EXPERIMENT_PLAN.md  personas.json  recruiter_identities.json  candidate_identities.json
conversation_style_profile.json  scenarios.jsonl  SCENARIO_MANIFEST.json
cost_model.json  fixtures_generated/  runner/
out/preflight_10/  out/checkpoint_50/  out/full_200/
  raw_traces/ normalized_traces.jsonl execution_gaps.json distribution_audit.json
  diversity_audit.json tool_coverage.json state_isolation_audit.json
  cost_report.json dead_letter.jsonl run_attempts.log RUN_MANIFEST.json
  REPORT.md REPORT.html
```

## 14. Final report answers the 8 source questions + the diversity gates from §11.
```
