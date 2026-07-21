# TODO — HR AI Traffic Generator (checkpointed)

Each checkpoint (CP) is a reviewable, revertible commit. Do not start the next CP
until the current one's acceptance passes. Plumbing changes to hr_ai core get a
`.decision-memory/` entry. Core behavior stays frozen (EXPERIMENT_PLAN §0).
Doubt-driven findings are folded in (tagged `[Dn]` = review finding number).

Status: `todo` / `wip` / `done` / `blocked`.

> **CP0–CP9 below are DONE** — baseline 200-corpus generated + run (qwen3-235b, temp=0,
> $4.39, out/run_200/). Active work is now the two diff experiments + enrichment. See
> `WORKING_MEMORY.md` for infra + gotchas.

---

## ACTIVE — post-200 diff experiments + enrichment

### T7 — Eval POC (DeepEval) · `todo` — DO right after the exporter-enrichment task
- Confirmed direction (this session): **DeepEval** (already in harness, pytest CI, no reshape);
  **POC = evals #1 + #2 first**; **DB-truth-first hybrid** (deterministic gates primary,
  LLM-judge only for fuzzy layer).
- #1 **Grounded-decision completion**: for decision-goal scenarios (family `decision_outreach`,
  and decision-implying `candidate_evaluation`), assert `decision_observable.committed` /
  final_decision in {shortlist,reject}. Task Completion (LLM-judge) as diagnostic layer.
- #2 **Identity resolution**: join trace `get_candidate_by_email` args vs the scenario's
  `candidate_identity_id` → `candidate_identities.json` email; score fraction using the
  correct in-context email (55% not-found today = the target).
- 4-bucket dataset (stratified ~60 / adversarial=security family ~15 / edge ~15 / failure-
  replays ~10), score PER-BUCKET, baseline↔candidate diff as the CI gate. Metrics as custom
  DeepEval BaseMetric over the enriched normalized traces.
- Later (#3 injection resistance G-Eval, #4 step efficiency, #5 faithfulness).

### T8 — Langfuse run-label fix · `todo` (small)
- HARNESS GAP: `hr_bridge` tags every run identically (`preflight`, `historical-traffic-v0`,
  case_id) → runs NOT separable in Langfuse except by `sessionId`. Add env `RUN_LABEL` flowed
  into `get_trace_config` tags + release/version so Langfuse is self-describing.

### T9 — 2 remaining STANDARD exporter fields · `todo`
- If we keep hand-rolling the JSONL: extract `stop_reason` (truncation flag, Bedrock returns
  it; confirmed absent from v2 JSONL) + `timeout` status. Everything else "requested" is
  eval-layer (T7), not capture — Langfuse already holds the standard set (see WORKING_MEMORY §7c).

## ACTIVE — post-200 diff experiments + enrichment

### T1 — Trace enrichment: token distribution + richer spans · `todo`
- Capture per-turn token breakdown: **system-prompt vs context/history vs memory vs tool-results vs reasoning vs completion** (currently only total in/out via usage_metadata).
- Approach: instrument `build_system_prompt` (count cached/stable vs dynamic block tokens), count message-history tokens, memory-injected tokens, tool-result tokens per turn (tiktoken/token count on each message component before the LLM call). Attach to the normalized trace event + Langfuse span metadata.
- Add any other enrichable signals to Langfuse/OTel spans (model-version hash, per-tool latency, tool-result sizes, injection-hit flags).
- **Acceptance:** each normalized turn event has a `token_breakdown{system,context,memory,tools,reasoning,completion}`; Langfuse spans carry it.

### T2 — Bounded parallelism in the runner · `todo`
- Add `PARALLELISM` env to `run_preflight.py`: ThreadPoolExecutor over scenarios (agent stream is sync). `cost_ledger` already thread-safe (`_lock`). Write records as futures complete; keep per-scenario try/except.
- Size concurrency to Bedrock TPM/RPM empirically (start 8, add exp-backoff on ThrottlingException). **More/bigger EC2 does NOT speed one run — Bedrock quota is the shared ceiling.**
- **Caveat:** parallel worsens cross-scenario state contention → use a separate DB per experiment (T3/T4).

### T3 — Exp A: MODEL CHANGE (own EC2 + own DB) · `todo`
- Same 200 corpus, agent model = **`qwen.qwen3-32b-v1:0`** (cheap, same family → isolates size effect). Simulator unchanged (Kimi/GLM). temp=0.
- Own EC2 (spawn a 2nd t3.small) + own Postgres **database** (same RDS, new dbname e.g. `exp_model`) → migrations+seed+seed_candidate_world into it; `DATABASE_URL` points there. Full isolation from Exp B.
- Output `out/run_200_qwen32b/`. **Kill the EC2 when done.**
- **Acceptance:** 200 run complete, diffable vs baseline `out/run_200/`.

### T4 — Exp B: CODE CHANGE (own EC2 + own DB) · `todo`
- Fix the **wrong-email lookup bug** (agent ignores provided email / hallucinates names — 33/200). Likely a prompt/tool tweak so the agent uses the exact email from context and doesn't invent one.
- Rerun same 200 on the SAME model (qwen3-235b), own EC2 + own DB (dbname `exp_code`). Output `out/run_200_codefix/`. **Kill EC2 when done.**
- **Acceptance:** 200 run complete; diff vs baseline shows lookup failures drop.

### T5 — Two-EC2 cost + isolation control · `todo`
- Spawn the 2nd EC2 (reuse instance-profile role, same tags), run A and B concurrently on separate boxes + separate DBs. Track spend (both Bedrock + EC2 hours from AWS credits). **Stop/terminate both boxes when done.**

### T6 — Diff engine · `todo`
- Compare baseline `run_200` vs each candidate on the SAME case_ids: decisions/scores/tool_sequences/token_breakdown deltas → a diff report per experiment.

---

## CP0 — Plumbing + AWS provisioning + pre-freeze probes · `done`

Goal: agent runs on AWS-native stack, behavior fixed, isolation understood, cost known.

**Provisioning (I ask before each AWS resource; you approve; I create via CLI):**
- [ ] Confirm AWS profile/creds available to the CLI.
- [ ] `[D16]` Verify Bedrock model availability + **TPM quotas** in candidate regions;
      pick region on availability+quota, not just price. Record chosen IDs + quotas.
- [ ] Provision **RDS Postgres** (free-tier). Provide `DATABASE_URL`. Run migrations + seed.

**Plumbing (hr_ai core):**
- [ ] `src/llm.py`: `build_chat_model` → `ChatBedrockConverse`. Per-call `model=`
      override kept. Env: region, `BEDROCK_AGENT_MODEL`, `BEDROCK_SQLGEN_MODEL`,
      agent temp + seed. `[D6]` low nonzero agent temp + logged seed; capture
      per-response model-version hash.
- [ ] Deps: `boto3`, `langchain-aws`, `deepeval` (pin), `+` embedding lib for
      diversity audit. Add to `requirements.txt`.
- [ ] Langfuse new keys, `ENABLE_LANGFUSE=true`; verify enriched trace lands.
- [ ] `ENABLE_NEMO_GUARDRAILS=false` → confirm true passthrough.
- [ ] `ENABLE_PROMPT_CACHE=false` (agent). `MAX_TOOL_CALLS_PER_TURN=25`.
- [ ] Enriched instrumentation: reasoning tokens + full tool args/results + token
      usage into spans (not shallow list). `[D12]` tag reasoning as opaque provenance.

**Harden memory (NOT paused) `[user]`:**
- [ ] Harden the memory subsystem, freeze its behavior before the manifest.

**Pre-freeze probes (blocking):**
- [ ] `[D7]` **Keying probe:** determine what memory/dedup key on (synthetic ID vs
      content/fuzzy/embedding). Decide concurrency rule for same-candidate scenarios.
- [ ] `[D7]` Build a **contamination canary** scenario.
- **Accept:** one chat + one evaluate land enriched traces on Bedrock+RDS; agent
  output shape unchanged vs OpenRouter sanity check; keying behavior documented.
- **Commit:** `plumbing: Bedrock + RDS, harden memory, enrich traces, keying probe`

## CP1 — Harness scaffold + inventory + cost model · `todo`

- [ ] `runner/` package + config (budget $45, concurrency=TPM-sized, model IDs, region).
- [ ] Inventory agent/API/tools/fixtures/state-mutations/tracing → `inventory.json`.
- [ ] `[D4]` **Full cost model → `cost_model.json`** BEFORE freeze: real per-call
      token counts incl. reasoning + validator retries + 30 long scenarios. If
      projected > $45, cut scenario count honestly up front (stratified).
- [ ] Provenance seed → `RUN_MANIFEST.json` (SHA, lock hash, model+version IDs,
      DeepEval version, region, temp+seed).
- **Accept:** inventory prints full tool+mutation surface; cost model says 200 fits $45.
- **Commit:** `harness: scaffold + inventory + pre-freeze cost model`

## CP2 — Identity, persona & style banks · `todo`

- [ ] `[D14]` `conversation_style_profile.json` from the two seed corpora — verify
      enough volume for real aggregation; **no verbatim n-grams leak**; model
      dirtiness as clustered (domain-term/fatigue), not flat rate.
- [ ] `candidate_identities.json` — 24 (5 seeded + 19 synthetic): strong/avg/weak/
      ambiguous, gaps, conflicts, dupes, missing evidence, near-boundary. No real PII.
- [ ] `recruiter_identities.json` + `personas.json` — 24 / 8 archetypes / 3 each,
      ≥4 differing behavioral properties; not all cooperative.
- [ ] `fixtures_generated/` — synthetic resumes (PDF/DOCX), LinkedIn/website fixtures.
- **Accept:** validators pass (counts, spread, ≥4-property divergence, no PII,
  no n-gram leak); style profile compact + non-derivative.
- **Commit:** `harness: identity + persona + style banks + fixtures`

## CP3 — 200 frozen scenarios + manifest · `todo`

- [ ] Generate `scenarios.jsonl` (source schema).
- [ ] Quotas: 160/40 input; depth 100/70/30 **but shape drives length** `[D11]`;
      `[D13]` **upweight hard families** (deviation logged w/ rationale).
- [ ] Sharing groups (dedup/recurrence/memory/idempotency) marked, ordered,
      sequential; `[D15]` ≥2 alt orderings for a subset.
- [ ] Uniqueness gate: user-turn text (exact + embedding) **and** `[D5]` planned
      trajectory signature. Near-dups reported, not deleted.
- [ ] `SCENARIO_MANIFEST.json` + content hash. **FROZEN** after this point.
- **Accept:** distribution validator green; deviation logged; manifest hashed.
- **Commit:** `harness: freeze 200-scenario manifest + hash`

## CP4 — TurnGenerator + split validator · `todo`

- [ ] `DeepEvalBaseLLM` wrappers for the **rotated** Bedrock simulators (pin each
      model/temp/seed). `[D1]` document register-monoculture limitation.
- [ ] Scenario controller (immutable case truth).
- [ ] `ConversationSimulator` wiring: `model_callback(input, turns, thread_id)` →
      in-process runner; custom `simulation_graph` + `stopping_controller`;
      `max_user_simulations` = scenario turn budget.
- [ ] `[D3]` **Two-tier validator**: hard-reject only label-leak / scenario-fact
      violation / unauthorized tool instr; **ALLOW** typos/rudeness/**false beliefs**.
      retry once → `SIMULATOR_ERROR`. `[D9]` log first-attempt rejects.
- [ ] Simulator I/O logged separately from agent trace.
- **Accept:** one scenario runs end-to-end in-process; simulator obeys reactions;
      validator allows a seeded false-belief turn + rejects a seeded label-leak.
- **Commit:** `harness: TurnGenerator + two-tier validator`

## CP5 — Trace capture + normalization + isolation checks · `todo`

- [ ] Provider-neutral JSONL envelope from stream events + Langfuse spans.
- [ ] Reasoning-token + tool arg/result + token/cost capture; state-before/after.
- [ ] Execution-gap taxonomy + `execution_gaps.json`; DLQ writer; `run_attempts.log`.
- [ ] `[D7]` Run the contamination canary under concurrency; assert clean.
- **Accept:** a mutating multi-turn scenario yields a complete ordered normalized
      trace w/ state deltas + reasoning + typed status; canary clean.
- **Commit:** `harness: enriched normalized trace + isolation canary`

## CP6 — 10-case preflight · `todo`

- [ ] Run 10: chat, upload, mutation, multi-turn, ≥1 typed failure, ≥1 sharing group.
- [ ] Manual inspection raw + normalized. Fix only generator/instrumentation/
      isolation/schema (never scenario meaning, never labels).
- [ ] `[D4]` Reconcile real per-case cost vs `cost_model.json`; extrapolate to $45.
- **Accept:** 10 clean terminal records; cost on track for 200 < $45.
- **Commit:** `run: 10-case preflight`

## CP7 — 50-case checkpoint + audits (incl. diversity) · `todo`

- [ ] Run 50 (**randomized order**, stratified per-family reservation `[D4]`).
- [ ] Audits: distribution, dup rate, tool coverage, state leakage, cost, error
      taxonomy → the `*_audit.json` files.
- [ ] `[D2/D5/D10]` **`diversity_audit.json`**: trace-embedding effective-N, tool-seq
      n-gram entropy, realized-vs-labeled shape, persona archetype-recovery accuracy.
- [ ] Tune concurrency/backoff from observed throttling.
- **Accept:** no cross-scenario leak; distribution on track; **effective-N healthy
  (not collapsed)**; cost pacing < $45.
- **Commit:** `run: 50-case checkpoint + distribution + diversity audit`

## CP8 — Full 200 run · `todo`

- [ ] Remaining cases, randomized order, bounded concurrency + backoff + DLQ,
      $45 guard active. No change to frozen meaning. Preserve long/failed cases.
- **Accept:** 200 terminal records (incl. typed gaps); 160/40 + depth exact; DLQ
  drained/explained; spend < $45; every run attempt in `run_attempts.log`.
- **Commit:** `run: full 200-scenario corpus`

## CP9 — Final artifacts + report · `todo`

- [ ] `RUN_MANIFEST.json` complete (code/prompt/model+version-hash/tools/fixtures/
      simulator-rotation/schemas/flags/scenario-hash/cost/temp+seed/keep-rule).
- [ ] `[D1]` Real-vs-synthetic discriminator spot-check → reads-human gate result.
- [ ] `REPORT.md` + `REPORT.html`: 8 source questions + §11 diversity gates;
      separates agent vs simulator vs infra; honest limitations (register
      monoculture, reasoning-token unfaithfulness, single-ordering, synthetic ≠
      production demand). No production-demand claims.
- **Accept:** all source acceptance criteria + diversity gates satisfied/reported.
- **Commit:** `report: final corpus artifacts + audit + diversity report`

---

## Wire-time confirmations
- Bedrock region + exact model IDs (availability + TPM verified in CP0).
- RDS provisioned, `DATABASE_URL` set. New Langfuse keys set.
- Cost model (CP1) says 200 fits $45; else honest up-front count cut.

## Deferred / documented limitations (from doubt review)
- Register monoculture across Chinese models — measured (discriminator), not solved.
- Reasoning tokens opaque, non-faithful — provenance only.
- Single frozen ordering canonical (alt orderings only for a subset).
- temp>0 → near-, not bitwise-, reproducibility; version hash pinned.
