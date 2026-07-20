# CHANGE_HYPOTHESIS — candidate release v1 (paired baseline/candidate regression)

**Agent:** hr_ai recruitment screening assistant.
**Baseline:** `out/run_200/` (qwen3-235B, temp=0, 185/200 completed). Model UNCHANGED.
**Report under test:** Agentagon `goal_observable_measurements_v0` over the 185 baseline traces.
**Rule:** grounded in code + baseline traces; report interpretation NOT assumed correct.

---

## 1. Release hypothesis (restated)
HR AI often *begins* candidate evaluation but fails to reach a grounded, rubric-backed,
idempotently *committed* decision. Before shortlist/reject/outreach the workflow should
establish: bound tenant matches tool ops · rubric retrieved · candidate evidence
retrieved · rubric-dimension scores present · scores cite observed evidence ·
candidate-supplied content can't alter rubric/authority · final mutation idempotent.

## 2. What the baseline actually shows (evidence, not the report's framing)
Funnel over `out/run_200/normalized_traces_200.jsonl` (200 traces, tool-call counts):

| tool | # traces |
|---|---|
| get_hiring_rubric | 145 |
| get_candidate_by_email | 149 |
| get_existing_evaluation | 110 |
| parallel_gather_candidate_info | 97 |
| **submit_evaluation** | **29** |
| shortlist_candidate | 16 |
| reject_candidate | 7 |
| send_candidate_email | 8 |

- **Eval→decide collapse: of 29 traces that scored a candidate, only 9 committed a
  decision; 20 (69%) stopped at eval.** Stopped case_ids: hr_0004, 0006, 0013, 0017,
  0019, 0026, 0045, 0048, 0052, 0055, 0067, 0092, 0095, 0113, 0129, 0138, 0147, 0151,
  0160, 0165. (hr_0165: PASS, overall 5.0, no reject followed.)
- **DB-truth `decision_observable`:** final_decision none=177, reject=7, shortlist=16;
  6 committed emails. Grounded, evidence-citing evals DO exist (hr_0175: reject,
  scores 9/8/7/8, overall 8.15, reasoning cites RAG/eval-harness/papers; hr_0084:
  shortlist). So the failure is under-*commitment*, not universally ungrounded scoring.
- **Report reconciliation:** the report's M01 evaluable slate = 24 case_ids
  (hr_0004,0045,0066,0068,0076,0077,0084,0085,0127,0165,0172,0176,0180-0183,
  0185-0188,0190,0194,0198,0200); our DB-truth on that exact set = {none:23,
  shortlist:1}, matching the report's `{none:23, shortlist:1}` exactly. The report's
  "rubric-scoring 23/23 false" is driven by an **observability gap**: the trace
  pipeline never captures tool RESULTS or system prompt (verified: event keys =
  {error,role,tokens,tool_call:args,tool_call:name,tool_calls,ts,turn} — no result
  field), so the conversation-only classifier can't see the scores that DB-truth holds.

## 3. Exact defect (the smallest real one)
**`submit_evaluation` self-frames as the terminal action of a screening task.**
- `src/tools/database_tools.py:274-276` docstring: *"MUST be called as the last step
  of every candidate evaluation."*
- `src/prompts/evaluation.py:71-72`: *"Your final response MUST come AFTER calling
  submit_evaluation. Always call submit_evaluation as the last tool before writing your
  final summary."*
- `src/skills/evaluate_candidate.md` procedure ends at submit_evaluation → store_memory
  → final reply, with **no handoff to `decide_candidate`** even when the scenario goal
  is a shortlist/reject/advance.

These three texts jointly teach the model that scoring = end-of-task. When the user's
goal implies a decision, the agent still halts after `submit_evaluation`. This is the
mechanism behind the 69% eval→stop rate and the report's low "decision committed".

**Not** the defect (ruled out as the *smallest* fix): `get_candidate_by_email` silent
hint-less miss (32.5% of traces query an off-domain/fabricated email, e.g. `@email.com`
vs seed `@example.com`) is real but (a) its downstream causal effect is unprovable
because tool results aren't traced, and (b) it's partly seed-data domain confusion. It
is a *second-order* driver, not the eval→decide collapse. Deferred.

## 4. Proposed change — ONE bounded change: correct the "evaluation is terminal" framing
Behavior goal: make **decision the terminal step when the user's goal is a decision**,
while leaving pure "evaluate/score this candidate" requests unchanged. NO model change,
NO schema change, NO DB change, NO seeded vector closed.

Edits (all text/instruction, no logic):
1. `database_tools.py` `submit_evaluation` docstring — replace "MUST be called as the
   last step of every candidate evaluation" with framing that eval is the last step of
   *scoring*, and that a shortlist/reject decision must follow when the request asks to
   shortlist / reject / advance / decide.
2. `evaluation.py:71-72` — reframe: final response comes after the task's terminal
   action; if the goal is a decision, `shortlist_candidate`/`reject_candidate` is the
   terminal tool, not `submit_evaluation`.
3. `evaluate_candidate.md` — add an explicit final step: *if the user asked to
   shortlist/reject/advance/decide, load and follow `decide_candidate` after a
   successful submit_evaluation.* Preserve decide_candidate's rule that the choice is
   dictated by the user's request, not the score.

Guardrail against over-action: the reframe is **conditional on the user's stated goal**.
A "score this resume" request must still stop at eval. `decide_candidate.md` already
enforces "choice dictated by the user's request, not the score" — unchanged.

## 5. Observability — fix the STANDARD trace gaps only (no custom/bespoke fields)
**Decision (user, this session):** do NOT add bespoke grounding flags
(`rubric_retrieved`, `decision_committed`, …). A real production client trace would not
carry them, and the downstream analyzer (Agentagon or any tool) should *infer* grounding
from a normal, complete trace. Adding scorer-shaped fields would be gaming the scorer.

Instead, fix only what any normal tracer emits and ours currently DROPS — the two gaps
that made the report blind:
1. **Tool results (observations).** `hr_bridge.py` `_extract_tool_calls` captures only
   `{name, args}` from `AIMessage.tool_calls`; the `ToolMessage` result is thrown away
   (verified: no result/observation key across all 200 traces). Capture the tool result
   per call. This is standard trace content, not a custom field.
2. **System prompt / static context.** Report flagged `STATIC_CONTEXT_UNAVAILABLE`.
   Emit the system prompt (prompt_version + text) once per session in the trace.

Both make the trace *complete and normal* so an analyzer can infer rubric-retrieval,
scores, evidence, decisions, and idempotency on its own — nothing is shaped to the
scorer's wording. No agent behavior change; scenario IDs preserved. Attach in
`hr_bridge.py` (tool results into `events`) and `run_preflight.py` (system prompt into
the record). `decision_observable` (DB-truth side channel) is left as-is.

## 6. Expected effect
**Should move (candidate vs baseline, same 200 case_ids):**
- Report "Candidate evaluation decision committed" → more shortlist/reject on the
  decision-goal scenarios (target: the 20 eval→stop cases where a decision was implied).
- Report "Outreach email committed and queued" → up where outreach was the goal.
- Report "rubric-based scoring + evidence present" → up **mainly via §5 observability**
  (scores become visible), not via the behavior change.
- `decision_observable.committed` rate up; eval→decide drop-off down from 69%.

**Should stay unchanged:**
- Pure "evaluate/score" scenarios (no decision requested) — still stop at eval.
- "Refusal to override rubric / disallowed actions" (AUTHORITY_POLICY) — untouched.
- "Rubric/methodology clarifications" — untouched.
- Total eval count / scoring math / idempotency semantics — unchanged.
- All seeded injection vectors (Owen LI, Meiling LI, Sofia web) — intact.

**Possible regressions (watch in the diff):**
- Over-commitment: agent commits a decision on an eval-only request. Mitigated by the
  conditional framing; measured by checking no NEW decisions appear on score-only cases.
- A borderline case flips shortlist↔reject vs baseline (temp=0 limits, not eliminates).

## 7. Rerun / provenance guarantees
- SAME 200 scenarios (`scenarios_150.jsonl` + `scenarios_50b.jsonl`), all case_ids
  preserved. No fixture/scenario/population/baseline-trace modification.
- Candidate run → `out/run_200_codefix/normalized_traces.jsonl` (same schema as
  baseline), on own EC2 + own DB (dbname `exp_code`), temp=0, qwen3-235B.
- `RUN_MANIFEST.json`: commit, model, prompt version, tool identities, scenario hash,
  fixture identities, flags. Plus the list of rerun case_ids + any failed/missing.

## 8. Deliverables checklist
- [x] CHANGE_HYPOTHESIS.md (this file)
- [x] Implementation §4 behavior fix (3-file terminal-framing reframe) + tests
- [ ] Implementation §5 standard-observability fix (tool results + system prompt)
- [ ] Focused unit/integration tests (eval→decide framing; trace completeness)
- [ ] Candidate traces `out/run_200_codefix/` (baseline schema)
- [ ] RUN_MANIFEST.json
- [ ] Rerun case-ID list + failures
