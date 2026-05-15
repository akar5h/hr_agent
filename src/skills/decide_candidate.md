---
name: decide_candidate
description: Shortlist or reject a candidate with idempotency and a written audit trail.
version: 1
triggers:
  - "shortlist <candidate>"
  - "reject <candidate>"
  - "move forward with <candidate>"
  - "decline <candidate>"
tools:
  - get_candidate_by_email
  - get_existing_evaluation
  - shortlist_candidate
  - reject_candidate
---

## When to use

The user is asking you to make a hire/no-hire decision on a candidate who has already been evaluated. Phrasings include "shortlist", "reject", "move forward", "advance to interview", "decline", "pass on".

## Inputs you need

- Candidate identifier — either `candidate_id`, or a name/email you can resolve
- `position_id` — the role they're being decided for
- `client_id` — already bound; do not change
- `session_id` — already bound; do not change
- A short `reason` for the audit log (one sentence is fine)

## Procedure

1. If you only have a name or email, call `get_candidate_by_email(email=<email>, client_id=<bound>)` to resolve the canonical `candidate_id`. If the candidate is not in this client's records, stop and tell the user — do not decide on candidates from other clients.
2. Call `get_existing_evaluation(position_id=<id>, client_id=<bound>, candidate_id=<id>)`. If `exists` is false, stop here and tell the user the candidate must be evaluated first. Decisions without evaluations are not allowed.
3. Call either `shortlist_candidate(candidate_id=..., position_id=..., client_id=<bound>, reason=..., session_id=<bound>)` or `reject_candidate(candidate_id=..., position_id=..., client_id=<bound>, reason=..., session_id=<bound>)`. The choice is dictated by the user's request, not the score.
4. Read the returned `idempotent_replay` flag. If true, the decision was already made earlier in this session — tell the user it was a no-op replay so they don't think a second decision was recorded.

## Gotchas

- These tools are idempotent on (action, session_id, candidate_id, position_id). Calling shortlist twice in the same session is a no-op replay, not an error.
- The audit row is written automatically by the tool — do not try to write to `write_database` for `candidate_decisions`.
- Per-session quota: 20 shortlist + 20 reject. If you hit the cap the tool returns `success: false` with a quota error.
- A `shortlist` followed by a `reject` (or vice versa) for the same candidate is allowed and produces a second decision row. The user is overriding; both decisions are kept for audit.
- Do NOT call `query_database` to verify the decision was recorded — trust the tool return.
