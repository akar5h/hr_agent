# Paired regression DIFF REPORT — baseline vs candidate + model change

Baseline (frozen): `out/run_200/` — v0 agent, **qwen3-235B**, temp=0, 185/200 completed, $4.39.
All diffs use **DB-truth `decision_observable`** (harness-independent) on the SAME 200 case_ids.
Both new runs use the improved harness (tool_results + system_prompt captured); the added
fields don't affect the compared fields.

Method note: qwen3-235B/32B are MoE, NOT bitwise-deterministic even at temp=0. Single-sample
runs → small deltas may be sampling noise. Directions are reported; magnitudes are one draw.

---

## Experiment B — CODE change (candidate release v1): eval→decide reframe
`out/run_200_codefix/` — v0→**v1 agent**, SAME model (235B), commit 69096f0, DB exp_code.
Isolates the behavior fix.

| metric | baseline v0 | candidate v1 | Δ |
|---|---|---|---|
| completed | 185 | 189 | +4 |
| reached submit_evaluation | 29 | 39 | +10 |
| committed decisions | 23 | 25 | +2 |
| of evaluators, committed | 9/29 (31%) | 14/39 (36%) | +5pp |
| **eval→STOP rate** | **69%** | **64%** | **−5pp** |
| committed emails | 6 | 4 | −2 |
| M01 goal slate (24) | none23/short1 | none22/short1/**rej1** | 1 flip (hr_0186 none→reject) |

**Read:** directionally correct at every decision stage, **no regressions** (no ungrounded
double-decisions; emails went down not up → no over-action). But **magnitude is small** (−5pp
eval→stop) and partly within MoE/temp noise. The conditional prompt/skill reframe is a soft
lever by design. Verdict: defensible ship; a decisive effect would need a harder mechanism or
N-sample confirmation.

## Experiment A — MODEL change: qwen3-32B (no code change)
`out/run_200_qwen32b/` — v0 agent, **32B**, DB exp_model, box i-007. Isolates model size.

| metric | baseline 235B | 32B | Δ |
|---|---|---|---|
| completed | 185 | 191 | +6 (more stable) |
| harness errors | 15 | 9 | −6 |
| tool-flail (≥20 calls) | 11 | 1 | −10 |
| **cost** | $4.39 | **$2.54** | **−42%** |
| reached submit_evaluation | 29 | 69 | +40 |
| committed decisions | 23 | 18 | −5 |
| distinct tool-seqs | 151/185 | 158/191 | ~same |
| search_web overuse | 96 | 85 | −11 |

**Read:** 32B is **cheaper (−42%), more stable, far less flailing**, similar trace diversity,
clean agent (no markup leak / non-termination). Behaviorally it **evaluates 2.4× more but
commits fewer decisions** (18 vs 23) → its eval→decide gap is *worse* (~74% stop). Smaller model
scores eagerly, commits reluctantly.

---

## Rerun coverage
- Both candidates: **200/200 case_ids present**, all scenario IDs preserved (no scenario/fixture/
  baseline-trace modification).
- codefix: 189 COMPLETED, 11 HARNESS_ERROR (empty-turns/loops class).
- qwen32b HARNESS_ERROR (9): hr_0011, hr_0044, hr_0058, hr_0090, hr_0100, hr_0104, hr_0132,
  hr_0162, hr_0184.
- baseline HARNESS_ERROR (15) unchanged (frozen).

## Provenance
`RUN_MANIFEST.json` in each out dir (commit, model, prompt_version, tarball+md5, dbname, box,
scenarios, flags). Boxes stopped post-run. Orphan i-071 pending manual teardown.
