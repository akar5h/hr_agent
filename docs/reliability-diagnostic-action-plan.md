# Candidate Screening Reliability Action Plan

Source: Kairos diagnostic report over 93 traces, with 67 mapped Candidate Screening traces and a 25% pass rate.

## Diagnosis

The workflow is weak because the core screening path is still model-directed. The LLM can repeat deterministic tools, ignore earlier tool outputs, obey adversarial resume text, or stop before a persisted side effect is complete. The trace export confirms this shape:

- `redundant_execution` affects 12 traces, especially repeated `query_database` calls.
- `context_ignored` appears around `parse_resume` output, with evidence of truncated prior tool output and reasoning that likely follows poisoned or hidden PDF text.
- Evidence coverage is good for tool calls and outputs, but missing for `system_prompt`, `retrieval_chunks`, `memory_events`, `graph_node`, and version tags.
- There are no reference traces, so regressions are hard to distinguish from normal variance.

## Implemented First Fixes

1. Fixed `parallel_gather_candidate_info` so it passes `linkedin_url` to `fetch_linkedin`.
2. Added resume-output hardening:
   - invisible/tiny/off-page PDF character filtering;
   - suspicious instruction-line redaction;
   - explicit `BEGIN_UNTRUSTED_RESUME_TEXT` / `END_UNTRUSTED_RESUME_TEXT` boundaries;
   - parser warning metadata for hidden text and suspicious instructions.
3. Added typed read tools for common screening checks:
   - `get_candidate_by_email`;
   - `get_existing_evaluation`.
4. Added per-turn duplicate suppression for deterministic tools, including `query_database`, `parse_resume`, `get_hiring_rubric`, and `parallel_gather_candidate_info`.
5. Made `submit_evaluation` idempotent by deriving a stable evaluation ID from client, position, candidate, and session.
6. Added prompt construction caching for repeated agent rebuilds, with `PROMPT_VERSION=candidate-screening-v2-reliability`.
7. Added trace metadata fields for workflow diagnostics:
   - `workflow`;
   - `condition`;
   - `graph_node`;
   - `agent_version`;
   - `prompt_version`;
   - `tool_version`.
8. Implemented the first bounded `StateGraph` Candidate Screening workflow and routed structured evaluation entry points through it:
   - FastAPI `POST /sessions/{session_id}/evaluate`;
   - Streamlit `Start Evaluation`.

## 2026 Agent Architecture Correction

Do not turn Candidate Screening into a brittle fixed script. The stronger 2026 pattern is a **durable workflow shell with bounded agentic nodes**:

- The workflow owns state, ordering, retries, idempotency, and side-effect gates.
- The LLM is used only inside bounded nodes where judgment is needed, such as evidence synthesis and scoring rationale.
- Tools are typed contracts with input/output guardrails, not arbitrary actions the model can call forever.
- Every run emits trace metadata and spans for workflow, node, tool call, guardrail, model generation, and side effect.
- Reference traces and eval datasets are first-class regression tests.

For this repo, the first implementation is now a LangGraph `StateGraph` that preserves durable execution with the existing Postgres checkpointer, while keeping the evaluator model inside a specific scoring node rather than as the global controller.

Current screening contract:

```text
load_rubric
  -> gather_candidate_evidence
  -> sanitize_and_classify_evidence
  -> resolve_candidate_identity
  -> check_existing_evaluation
  -> score_candidate_with_bounded_agent
  -> validate_structured_evaluation
  -> submit_evaluation_once
  -> write_memory_summary
  -> final_response
```

Each node should write explicit typed state:

- `rubric_loaded`
- `evidence_gathered`
- `evidence_warnings`
- `candidate_identity`
- `existing_evaluation`
- `structured_scores`
- `evaluation_submitted`
- `memory_written`
- `final_response_ready`

The graph should reject final output until `evaluation_submitted.success == true`. It should also make duplicate deterministic work structurally impossible: if `rubric_loaded == true`, the graph cannot call `get_hiring_rubric` again for the same position; if `evidence_gathered == true`, the graph cannot re-run `parallel_gather_candidate_info` unless the input changed.

Recommended node responsibilities:

| Node | Owner | Notes |
|---|---|---|
| `load_rubric` | deterministic code | Typed DB read, no LLM. |
| `gather_candidate_evidence` | deterministic code | Parallel resume, LinkedIn, website fetch. |
| `sanitize_and_classify_evidence` | deterministic + guardrail model | Redact prompt injection, set confidence/warning flags. |
| `resolve_candidate_identity` | deterministic code | Tenant-scoped candidate lookup. |
| `check_existing_evaluation` | deterministic code | Idempotency and duplicate-screening check. |
| `score_candidate_with_bounded_agent` | LLM node | Receives only sanitized evidence and rubric; returns structured JSON. |
| `validate_structured_evaluation` | deterministic code | Schema, score range, rubric-weight checks. |
| `submit_evaluation_once` | deterministic code | Single idempotent write. |
| `write_memory_summary` | deterministic code or small LLM | Only after evaluation write succeeds. |
| `final_response` | LLM or template | Cannot run before side effects are complete. |

Guardrails should exist at the tool boundary, not just the outer chat boundary:

- before `parse_resume`: file type, size, path, tenant/session ownership;
- after `parse_resume`: hidden text, instruction text, truncation, parse confidence;
- before `query_database`: strongly prefer typed read tools; if retained, enforce SELECT-only AST and tenant filter;
- before side-effect tools: require current session tenant, position, candidate, and idempotency key;
- after side-effect tools: verify the committed row and include the row ID in state.

Trace every node as part of one Candidate Screening workflow trace. The minimum metadata should be:

- `workflow=Candidate Screening`
- `graph_node=<node name>`
- `condition=<success|guardrail_blocked|missing_side_effect|duplicate_suppressed|validation_failed>`
- `agent_version`
- `prompt_version`
- `tool_version`
- `candidate_id`
- `position_id`
- `client_id`

This keeps the system agentic where judgment is useful, but deterministic where correctness matters.

Remaining hardening for this architecture:

- add explicit human approval before outbound email side effects;
- replace fallback heuristic scoring with a required structured-output model in production mode;
- add per-node custom spans so trace exports show every graph node directly, not only final state metadata;
- add reference datasets and trace grading for the seven scenarios below.

## Reference Trace Set

Create reference traces for these cases:

- clean resume with LinkedIn and website;
- poisoned PDF resume with hidden instructions;
- missing LinkedIn fixture;
- duplicate candidate;
- existing evaluation;
- invalid position;
- successful shortlist after evaluation.

Each reference should include expected tool sequence, side effects, terminal response shape, and max step budget.

## Acceptance Metrics

- Candidate Screening pass rate rises above 80% on the reference suite.
- Duplicate deterministic tool calls per turn are zero unless args differ.
- `submit_evaluation` creates at most one logical evaluation per candidate/position/session.
- `parse_resume` flags and redacts known hidden/instruction payloads.
- Trace exports show nonzero coverage for `graph_node`, `condition`, and version metadata.
