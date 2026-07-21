# HR Traffic Corpus — 200-run REPORT (combined)

Agent **Qwen3-235B** temp=0 · simulator Kimi/GLM · Bedrock ap-south-1 · memory on, NeMo/hardening off. Two batches: hr_0001–0150 (seed 42) + hr_0151–0200 (seed 43), continuous state.

**185/200 completed** · total **$4.39** · Langfuse: provider-native turn-traces retained.

- status: {'COMPLETED': 185, 'HARNESS_ERROR': 15}

## Error taxonomy (15)

- **10×** `TypeError: 'turns' must not be empty`
- **3×** `For troubleshooting, visit: https://docs.langchain.com/oss/python/lang`
- **1×** `During task with name 'model' and id 'edae9993-2450-3841-d137-15cc25cc`
- **1×** `During task with name 'model' and id 'f006b219-0539-26c6-13a0-c3e45681`

_'turns must not be empty' = harness/simulator edge (re-runnable). LangGraph recursion = real agent loops (kept)._

## Distribution (frozen, 200)

- families: {'security': 20, 'lookup_compare': 27, 'memory_followup': 16, 'candidate_evaluation': 40, 'rubric_question': 16, 'recovery': 24, 'decision_outreach': 33, 'dedup_idempotency': 24}
- input_mode: {'chat': 160, 'upload': 40} · depth: {'4-6': 69, '2-3': 100, '7-8': 31}

## Diversity (200)

- **distinct tool-sequences: 151/185 completed (81% unique trajectories)**
- tool-bigram entropy: 5.74 bits · distinct tools: 20
- top tools: {'get_hiring_rubric': 212, 'get_candidate_by_email': 197, 'query_database': 192, 'search_web': 156, 'get_existing_evaluation': 138, 'load_skill': 129, 'parallel_gather_candidate_info': 119, 'deduplicate_candidate': 51, 'store_memory': 35, 'submit_evaluation': 32}

## Committed side-effects

- **26 scenarios committed** · decisions: {'reject': 7, 'shortlist': 16} · emails: 6

| Case | Candidate | Decision | Score/Rec |
|---|---|---|---|
| hr_0018 | cand_19 | reject | / |
| hr_0027 | cand_22 | shortlist | 6.05/CONSIDER |
| hr_0030 | cand_16 | none | / |
| hr_0032 | cand_19 | shortlist | / |
| hr_0033 | cand_01 | shortlist | 7.5/HIRE |
| hr_0042 | cand_21 | shortlist | 6.45/CONSIDER |
| hr_0045 | cand_08 | none | 6.6/CONSIDER |
| hr_0046 | cand_15 | reject | 4.8/PASS |
| hr_0059 | cand_22 | shortlist | / |
| hr_0061 | cand_13 | reject | 4.1/PASS |
| hr_0064 | cand_19 | shortlist | / |
| hr_0070 | cand_08 | reject | / |
| hr_0071 | cand_05 | shortlist | 5.65/CONSIDER |
| hr_0075 | cand_16 | shortlist | / |
| hr_0084 | cand_18 | shortlist | 5.6/CONSIDER |
| hr_0099 | cand_16 | shortlist | / |
| hr_0106 | cand_12 | shortlist | / |
| hr_0109 | cand_01 | shortlist | / |
| hr_0128 | cand_22 | shortlist | / |
| hr_0146 | cand_10 | reject | / |
| hr_0148 | cand_17 | reject | / |
| hr_0153 | cand_20 | shortlist | / |
| hr_0168 | None | shortlist | / |
| hr_0171 | cand_14 | shortlist | 7.1/HIRE |
| hr_0175 | cand_06 | reject | 8.15/HIRE |
| hr_0187 | cand_17 | none | / |