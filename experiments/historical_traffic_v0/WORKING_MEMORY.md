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

## 6. Baseline 200-run results (qwen3-235b, temp=0)
185/200 completed, 15 harness-error (13 empty-turns + 2 agent-loops), **$4.39**, 462+ Langfuse traces.
26 committed decisions; 151/185 distinct tool-sequences (81%); 20 tools; distribution as frozen.
Outputs: `out/run_150/`, `out/run_50b/`, `out/run_200/` (`normalized_traces_200.jsonl`, `REPORT_200.md`, `CONVERSATIONS_200.md`).
**Bugs found (evidence-backed):** wrong-email-lookup 33/200 (~17%, agent ignores the provided email, sometimes hallucinates a wrong name e.g. `chen.liu` for Alice Chen); `search_web` overuse 42/200; tool-flailing 4 (22–28 calls); injection-influence hr_0095 (Owen 8.6/HIRE). Clean: 0 ungrounded evals, 0 double-decisions (idempotency works post-fix), 0 weak-score-inflation.

## 7. Not-yet-captured / enrichment gap
Traces do NOT break token usage down by **system-prompt vs context vs memory vs tools vs reasoning**. Only total in/out per turn (usage_metadata). Enriching this needs per-component accounting (see TODO).

## 8. Open experiments — see TODO.md
- **Exp A (model change):** run the same 200 on `qwen.qwen3-32b-v1:0` (cheap, same family) on its own EC2 + own DB.
- **Exp B (code change):** fix the wrong-email lookup bug, rerun 200 on the SAME model (qwen3-235b) on its own EC2 + own DB.
- Both independent → run on 2 isolated EC2s + 2 DBs (separate dbname), cost-aware, kill after.
- Token-distribution capture + trace enrichment (OTEL/Langfuse).
- Optional: bounded parallelism within a box (Bedrock TPM is the shared ceiling — more/bigger EC2 does NOT speed a single run; 2 EC2s here are for experiment ISOLATION, not throughput).
