---
name: rank_position
description: Run the ATS ranker across all evaluated candidates for a position.
version: 1
triggers:
  - "rank candidates for <position>"
  - "sort candidates by score"
  - "who are the top candidates"
  - "compare candidates for <position>"
tools:
  - trigger_ats_ranking
---

## When to use

The user wants a ranked or sorted view of candidates for a position. Phrasings include "rank", "sort", "compare", "top candidates", "leaderboard", or "who's at the top".

## Inputs you need

- `position_id` or position title
- `client_id` — already bound; do not change

## Procedure

1. Call `trigger_ats_ranking(position_id=<id-or-title>, client_id=<bound>)`. The ATS sub-agent handles the rubric lookup, fetches all evaluated candidates for the position, applies the weighted scoring policy, and returns a ranked summary.
2. Relay the returned summary to the user with a brief framing sentence. Do not re-rank or re-score — the sub-agent is authoritative.

## Gotchas

- This skill is a thin wrapper. The heavy lifting lives in the ATS sub-agent — do not pre-fetch candidates yourself with `query_database`.
- If the rubric is missing for the position, `trigger_ats_ranking` returns "Rubric not found..." — relay that to the user and ask them to confirm the position id or title.
- Do not call this for a single candidate — use `evaluate_candidate` instead.
- The ATS sub-agent already has its own trace; this call shows up as `workflow=ATS Ranking` in dashboards.
