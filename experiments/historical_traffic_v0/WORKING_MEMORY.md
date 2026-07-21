# WORKING MEMORY — HR AI Traffic-Generation Experiment

> Read this FIRST on any fresh session before touching the experiment. It captures
> the infra, the run mechanics, the hard-won gotchas, the harness design, results,
> and open tasks — so none of it gets rediscovered. Pair with `docs/plan-of-record.md`
> (decisions D1–D10) and `EXPERIMENT_PLAN.md` / `TODO.md`.

## 0. What this is
Disposable harness that drives the **unchanged** hr_ai recruitment agent through
frozen conversation scenarios via DeepEval's `ConversationSimulator`, capturing an
enriched trace corpus (Langfuse + normalized JSONL) for later regression/diff work.
Agent = a multi-tenant recruitment-screening assistant (evaluate/compare/rank
candidates vs a client rubric; shortlist/reject/outreach; tenant-scoped).

## 1. AWS infra (all ap-south-1, account 854847082059, CLI profile `akarsh-deepprobe`)
- **EC2 dev/run box:** `i-016a9a231189e0894` (t3.small), **SSM-reachable, NO SSH / NO inbound**.
  Default VPC `vpc-04842890ff94f6fae`, subnet `subnet-0e1d4ceb6b426fe8f` (public, 1a),
  SG `sg-0132efc7294b9015c` (default, self-referencing → reaches RDS). Instance profile
  role `hr-traffic-bedrock-ec2-role`. Code lives at `/opt/hrai`. **Stop it when idle to save credits.**
- **RDS (current):** `db-pg-1.c10aayg0ym7j.ap-south-1.rds.amazonaws.com` — Postgres,
  **password auth**, private, default VPC/SG, `sslmode=require`. Reachable from the box only.
  - The OLD `database-1` cluster was **IAM-auth-only (PAM)** — static passwords impossible → ABANDONED. Don't use it.
- **Langfuse:** cloud `https://us.cloud.langfuse.com`. Keys in SSM.
- **S3:** `hr-traffic-v0-854847082059` (`code/` = deployed tarball + scripts; `out/` = run artifacts).
- **SSM Parameter Store (SecureString):** `/hr-traffic-v0/DATABASE_URL`,
  `/hr-traffic-v0/LANGFUSE_SECRET_KEY`, `/hr-traffic-v0/LANGFUSE_PUBLIC_KEY`,
  `/hr-traffic-v0/LANGFUSE_BASE_URL`. Box reads these at runtime; secrets never in S3/git.
- **Bedrock models verified in ap-south-1** (access granted, no console gate):
  `qwen.qwen3-235b-a22b-2507-v1:0`, `qwen.qwen3-32b-v1:0`, `moonshotai.kimi-k2.5`,
  `moonshot.kimi-k2-thinking`, `zai.glm-4.7`, `zai.glm-4.7-flash`, `deepseek.v3.2`.
- **IAM:** provisioner user `hr-traffic-provisioner` (my `akarsh-deepprobe` principal, inline policy);
  instance role `hr-traffic-bedrock-ec2-role`. JSONs in `aws/`. **`bedrock:Converse`/`ConverseStream`
  are NOT IAM actions** — grant `bedrock:InvokeModel` + `InvokeModelWithResponseStream`.
- **Local CLI:** use miniforge aws `/Users/akarshgajbhiye/miniforge3/bin/aws` (pyOpenSSL was
  upgraded to fix it). `session-manager-plugin` at `/opt/homebrew/bin`. Homebrew `aws` is BROKEN
  (python3.14 pyexpat dlopen). Always `export PATH=/Users/akarshgajbhiye/miniforge3/bin:$PATH` +
  `export AWS_PROFILE=akarsh-deepprobe`.

## 2. Run mechanics (the operational loop)
1. **Deploy:** `tar` the hr_ai repo (exclude `.venv .git __pycache__ *.pyc .pytest_cache .idea data logs .env .mypy_cache`) → `aws s3 cp` to `s3://hr-traffic-v0-854847082059/code/hrai_code.tgz` → box extracts to `/opt/hrai`.
2. **Box env:** `/opt/hrai/env.sh` (an S3 artifact `code/env.sh`, copied to box) fetches SSM secrets at source-time and exports `BEDROCK_*` + flags. **`source /opt/hrai/env.sh` before any run.**
   Contains: `BEDROCK_AGENT_MODEL`, `BEDROCK_SQLGEN_MODEL`, `BEDROCK_FALLBACK_MODEL`,
   `BEDROCK_FORCE_TEMPERATURE=0`, `ENABLE_LANGFUSE=true`, `ENABLE_HARDENING=false`,
   `ENABLE_NEMO_GUARDRAILS=false`, `ENABLE_PROMPT_CACHE=false`, `MAX_TOOL_CALLS_PER_TURN=25`.
3. **Drive the box:** `aws ssm send-command --document-name AWS-RunShellScript`. For long runs:
   write a `.sh` to S3, box pulls + runs it `setsid`-backgrounded, tees a log, syncs `out/<subdir>` to S3, `touch <DONE marker>`. Poll the marker with a send-command that does a box-side `sleep N` then checks.
4. **Run harness:** `cd /opt/hrai && source env.sh && export SCENARIOS_FILE=<f> OUT_SUBDIR=<d> && /opt/hrai/.venv/bin/python -u experiments/historical_traffic_v0/runner/run_preflight.py`.
   Env knobs: `SCENARIOS_FILE`, `OUT_SUBDIR`, `PREFLIGHT_LIMIT` (first N), `PREFLIGHT_CASES` (csv case_ids).
5. **Python on box:** `/opt/hrai/.venv` (python3.11). Deps: repo `requirements.txt` + `deepeval` +
   `psycopg2-binary` (SQLAlchemy migrations need it) + `boto3`/`langchain-aws`.

## 3. GOTCHAS — do NOT rediscover these
- **SSM send-command quoting hell.** Nested quotes / heredocs / shell globs in `--parameters commands='[...]'` BREAK (zsh glob errors, mangled `set -e`→`set -d`). **Always** put non-trivial scripts in a file → S3 → box `aws s3 cp` + `bash`. Keep the inline command a trivial one-liner.
- **NEVER name a box script `inspect.py`** (or any stdlib name). It shadows stdlib `inspect` for everything in `/opt/hrai`, breaking langchain imports with a confusing circular-import error. `rm -f /opt/hrai/inspect.py` if it ever appears.
- **PYTHONPATH:** ad-hoc scripts run standalone need `PYTHONPATH=/opt/hrai` (or `cd /opt/hrai` for `python -c`, whose cwd is on the path). `run_preflight.py` inserts its own sys.path; ad-hoc ones don't.
- **DeepEval wiring (v4.1.1):** `model_callback` param MUST be named `input`; must return a `Turn(role="assistant", content=...)`; use `async_mode=False`; run ONE golden per scenario (session isolation). `BedrockSimLLM.generate/a_generate` MUST detect a pydantic schema arg and route to `generate_with_schema` — else the controller does `str.is_complete` → crash.
- **Model choice:** **DeepSeek-V3.2 DROPPED as agent** — leaks `<｜DSML｜function_calls` markup into the text channel AND fails to terminate the ReAct loop (hits 50-recursion). **Qwen3-235B = clean agent.** `temp=0` via `BEDROCK_FORCE_TEMPERATURE` for diffability (MoE isn't bitwise-deterministic even at 0 — documented limitation).
- **ON-CONFLICT bug (FIXED, D10):** `_record_decision`/`_record_email` needed `WHERE idempotency_key IS NOT NULL` on the `ON CONFLICT` (matches the partial unique index). Without it EVERY shortlist/reject/email silently failed ("no unique or exclusion constraint matching"). This is why `candidate_decisions` was 0 until fixed.
- **`search_web` = real Tavily** if `TAVILY_API_KEY` set (isolation risk). Currently UNSET on box → returns error, and the agent retries it wastefully (~42/200 scenarios). Should stub/mock like websites. (Isolation task.)
- **`WEBSITE_FIXTURE_MAP`** (`src/tools/website_scraper.py`): `scrape_website` hits the REAL internet if a hostname isn't in the map. All 24 candidate website hostnames were added → keep the map current or the experiment leaks.
- **State accumulation:** sequential scenarios reuse the 24 candidates → `evaluations` accumulate → later scenarios see earlier evals ("already evaluated") → nondeterminism. Parallelism WORSENS it. **For independent experiments use a SEPARATE Postgres database per experiment** (same RDS, different dbname) so their mutations don't collide.
- **Cost/threading:** `cost_ledger.py` monkeypatches `botocore._make_api_call` to tally `Converse` (simulator) usage; agent streaming (`ConverseStream`) usage is reported via `record_agent_usage` (usage_metadata). `_lock` guards the ledger — safe for threaded parallelism.
- **Shared code-tree contamination (do NOT run two DIFFERENT-code experiments on one box):** a separate DB per experiment is NECESSARY but NOT sufficient. Two runs on the same box share the `/opt/hrai` tree, and `load_skill()` reads `src/skills/*.md` FRESH from disk every call — so deploying experiment B's tarball (overwriting the tree) silently swaps experiment A's skill bodies mid-run. In-memory `.py` modules (system prompt `STABLE_INSTRUCTIONS`, imported tools) are NOT re-read, but skills ARE. Isolate different-code experiments by: separate BOX, or separate on-disk dir with its own `sys.path`, or run SEQUENTIALLY. Bedrock TPM is the shared ceiling anyway, so concurrent boxes give ~no speedup — sequential on one box is the simple correct default. (Learned 2026-07-20: codefix v1 tarball contaminated the concurrent qwen32b run's skills → qwen rerun.)

## 4. Harness files (`experiments/historical_traffic_v0/`)
- `runner/run_preflight.py` — main loop; per scenario `ConversationSimulator(model_callback=HrAgentBridge, simulator_model=BedrockSimLLM, async_mode=False).simulate([golden], max_user_simulations=budget)`; captures normalized trace + `decision_observable` (committed decisions/emails/eval scores, queried by session in DB) + `tool_sequence` + tokens/usd; writes `out/<OUT_SUBDIR>/normalized_traces.jsonl` + `cost_report.json`. Env-configurable (§2.4).
- `runner/hr_bridge.py` — `HrAgentBridge` (the `model_callback`): builds `build_agent(client_id, session_id)` once per session, runs one streamed turn per call (mirrors `server.py::_run_turn`), records usage, exposes `.events`/`.flush()`. `_augment_first_turn` injects the resume file PATH for `input_mode:upload` scenarios (in-process shared FS; for a REMOTE agent you'd POST the bytes to their `/upload` and reference the returned id).
- `runner/bedrock_sim.py` — `BedrockSimLLM(DeepEvalBaseLLM)` over Bedrock `converse`; simulator models Kimi-K2.5 / GLM-4.7 rotated (decorrelated from the Qwen agent).
- `runner/cost_ledger.py`, `runner/pricing.py` — token/USD accounting (ESTIMATE prices).
- `runner/gen_scenarios.py` (150, seed 42) + `runner/gen_scenarios_ext.py` (50, seed 43) → `scenarios_150.jsonl` + `scenarios_50b.jsonl` (+ manifests, frozen hashes).
- `runner/seed_candidate_world.py` — idempotent: seeds 24 candidates (email, client, position) into DB + prior evals for `already_evaluated`/`duplicate` archetypes. Run with `PYTHONPATH=/opt/hrai`.
- Banks: `candidate_identities.json` (24, emails `@example.com`/seeded `@email.com`), `recruiter_identities.json` (24 / 8 personas), `personas.json`, `conversation_style_profile.json`.
- Fixtures: `fixtures_generated/resumes/cand_01..24.docx`; `fixtures/linkedin/*.json`; `fixtures/websites/*.html` (bounded 3 injections: Owen cand_09 LI, Meiling cand_22 LI, Sofia cand_16 web).

## 5. Core hr_ai facts
- `build_chat_model` (src/llm.py) = the ONE model chokepoint → `ChatBedrockConverse`, per-call `model=` override, throttle fallback. Levels: agent+ATS+screening use `BEDROCK_AGENT_MODEL`; SQL-gen uses `BEDROCK_SQLGEN_MODEL`.
- HTTP surface exists (`server.py`): `POST /sessions`, `POST /sessions/{id}/chat`, `POST /upload`, `POST /sessions/{id}/evaluate`. We chose in-process for richer capture.
- Tools: `get_candidate_by_email`, `parallel_gather_candidate_info`, `fetch_linkedin`, `scrape_website`, `parse_resume`, `submit_evaluation`, `shortlist_candidate`, `reject_candidate`, `send_candidate_email`, `query_database`, `write_database`, `deduplicate_candidate`, `get_existing_evaluation`, `get_hiring_rubric`, `trigger_ats_ranking`, `store_memory`/`retrieve_memory`, `load_skill`. Decisions/emails write to `candidate_decisions`/`outbound_emails` keyed by session.
- Seeded clients: `client-techcorp`, `client-startupai`. Positions: `pos-techcorp-spe/sre/de`, `pos-startupai-mle/aie`.

## 6. Baseline 200-run results (ORIGINAL frozen, qwen3-235b, temp=0, OLD exporter)
185/200 completed, **$4.39**; 26 committed decisions. Output: `out/run_200/` originally held
`normalized_traces_200.jsonl` (v1 schema). **NOTE: `out/run_200/` was REGENERATED on the v2
exporter (see §9); the old v1 copies are preserved as `out/*_v1schema/`.**
**Bugs found (evidence-backed):** wrong-email-lookup ~33/200 (agent ignores/hallucinates the
provided email); `search_web` overuse; tool-flailing; injection-influence (Owen). Clean: no
ungrounded double-decisions.

## 7. SESSION HANDOFF — state as of 2026-07-21 (READ THIS)

### 7a. What shipped (branch `feat/bedrock-traffic-harness`, ~15 commits)
- **Candidate release v1 (D11):** "eval is not terminal" — decision follows eval when the
  user's goal is a decision. 3-file conditional reframe (submit_evaluation docstring +
  system prompt + `evaluate_candidate.md`), PROMPT_VERSION `candidate-screening-v3-decision-
  followthrough`. Commits `c02e161`(+`ff0d34e` test fix). NO model change. `CHANGE_HYPOTHESIS.md`.
- **Observability, standard fields only (user rule: NO bespoke scorer-shaped fields):**
  `ece0407` captured tool results + system prompt; `ca52bc8` enriched to **schema
  `normalized-trace-v2`**: per-tool `events[].tool_calls[]` = {name, arguments,
  result_summary(≤2000, resume-safety fields preserved), status, error, latency_ms, cached}
  via `workflow._wrap_tool`; `sub_agents[]` (ATS: model/input/ranked_output/ran_all_4_steps;
  SQL-gen: generated_sql/executed_ok/client_id_filter_present) via new leaf module
  `src/observability/trace_capture.py`. Pure observability — agent behavior unchanged.
- **3 paired 200-runs, twice.** v1-schema first (`b6eb981`), then ALL 3 regenerated on the
  v2 exporter (`14d2a78`). Baseline regenerated too.

### 7b. v2 DIFF (canonical, `DIFF_REPORT.md`, DB-truth decision_observable, 200 cases)
| run (v2) | box | model | DB | completed | eval | decisions | stop% | cost |
|---|---|---|---|---|---|---|---|---|
| baseline (v0 agent) | i-007 | 235b | exp_base | 186 | 37 | 17 | 76% | $4.63 |
| codefix (v1 agent) | i-016 | 235b | exp_code | 186 | 41 | 26 | 63% | $4.70 |
| qwen32b (v0 agent) | i-007 | 32b | exp_model | 189 | 62 | 12 | 89% | $2.51 |
- **KEY honest finding:** regenerated baseline = 17 decisions vs old frozen 23 (SAME v0 code)
  → MoE/temp-0 noise ~±6. Code fix (−13pp stop, +9 decisions) is ABOVE noise → real but
  single-sample. Do NOT overclaim. N-sample to make it decisive (parked).
- Traces at `out/run_200/`, `out/run_200_codefix/`, `out/run_200_qwen32b/`
  (`normalized_traces.jsonl` + `RUN_MANIFEST.json`). S3 mirror `s3://.../out/<run>/`.
  Tarballs: `code/hrai_v0agent_v2.tgz` (baseline+qwen), `code/hrai_v1agent_v2.tgz` (codefix).

### 7c. Langfuse — full data IS there, but runs are NOT tag-distinguished (GOTCHA)
- All v2 runs captured in Langfuse cloud (2785 traces, 14809 gens, 17:31–19:06 UTC 07-20).
  Langfuse holds the FULL span tree natively: TOOL spans (input args + output result +
  level/statusMessage), GENERATION (model + per-call token usage + latency + cost + input/
  output), **stopReason** (truncation), rich metadata (prompt_version, ls_temperature,
  langgraph_node, sessionId, userId). Verified by pulling `preflight-hr_0199`.
- **Only local stores:** the v2 normalized JSONL (a SUBSET — missing stopReason + per-LLM-call
  token breakdown) and Postgres (outcomes only). No local span store. Langfuse free tier =
  retention/ingestion limits → pull via API if durable-offline needed.
- **HARNESS GAP:** `hr_bridge` tags EVERY run identically: `tags=["preflight",
  "historical-traffic-v0", <case_id>]`, `condition="preflight"`, no release/version. So
  `historical-traffic-v0` = ALL runs (3511); no `codefix`/`qwen` tag exists. **The ONLY
  reliable run-label in Langfuse is `sessionId`** (`pf-hr_XXXX-<hash>`; hash differs per run).
  To split runs: take the 200 session_ids from a run's local JSONL → query Langfuse by
  sessionId. (qwen also separable by 32b-only model; baseline vs codefix ran concurrently,
  same tags/235b → session_id is the ONLY split.)

### 7d. Infra state NOW
- Boxes **i-016 + i-007 STOPPED** (project-tagged; restartable). Code trees: i-007 = v0 agent,
  i-016 = v1 agent (from the regen). exp DBs on RDS: `exp_base`, `exp_code`, `exp_model` (all
  seeded, reusable). Original baseline DB untouched.
- **ORPHAN `i-071d1a2189311a709` STILL RUNNING** — I launched it without `project=hr-traffic-v0`
  tag; provisioner IAM (`Ec2LifecycleOnlyProjectTagged`) can't stop/terminate untagged boxes.
  **USER must terminate with admin IAM:** `aws ec2 terminate-instances --region ap-south-1
  --instance-ids i-071d1a2189311a709`. (Also: provisioner CANNOT self-grant IAM — classifier-
  blocked; don't retry.)

## 8. NEXT TASKS (priority order — see TODO.md T7)
1. **T7 — Eval POC (DeepEval).** CONFIRMED plan: DeepEval (in harness, pytest CI, no reshape);
   POC = evals #1 (grounded-decision completion, DB-truth assertion + Task Completion) + #2
   (identity resolution: join `get_candidate_by_email` args vs scenario `candidate_identity_id`
   → `candidate_identities.json` email; 55% not-found is the target). DB-truth-first hybrid
   (deterministic gates primary, LLM-judge for fuzzy). 4-bucket dataset (stratified/adversarial=
   security family/edge/failure-replays), score PER-BUCKET, baseline↔candidate diff = CI gate.
   Later: #3 injection-resistance G-Eval, #4 step-efficiency, #5 faithfulness.
2. **Langfuse run-label fix** (small): add env `RUN_LABEL` → `get_trace_config` tags +
   release/version so Langfuse is self-describing (removes the §7c session_id join need). Future.
3. **Missing STANDARD exporter fields** (if we keep hand-rolling): `stop_reason` (truncation)
   + `timeout` status. Everything else "requested" is EVAL-layer detection (T7), not capture —
   see the observability audit (§7c): Langfuse already has the standard set.
4. Optional: N-sample the M01 goal slate to make the code-fix effect decisive vs the ±6 noise.

### Standard-vs-custom audit verdict (settled this session)
Requested "missing" trace fields are mostly STANDARD (Langfuse/OTEL native) — we missed them
only because the normalized JSONL is a hand-rolled reconstruction from the LangGraph stream.
The genuine customs (error-treated-as-success, recovery type, PII/entitlement leak, scores-in-
email, rage, task-evasion) are DERIVED EVAL SIGNALS (T7), not capture fields. Goal/expected =
dataset label (join from `scenarios_*.jsonl`). Standard way = consume the Langfuse/OTEL span
tree, don't reconstruct.
