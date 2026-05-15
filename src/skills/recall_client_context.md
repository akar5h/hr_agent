---
name: recall_client_context
description: Recall prior session preferences and consolidated context for a returning client.
version: 1
triggers:
  - "what do we know about <client>"
  - "remind me about this client"
  - "any prior context"
  - "what did we say last time"
tools:
  - retrieve_memory
---

## When to use

The user is asking about prior context, preferences, or history for the current client. Also use this proactively at the start of a session if it is clear the client is returning and you have not yet recalled their context.

## Inputs you need

- `client_id` — already bound; do not change
- `session_id` — already bound; do not change

## Procedure

1. Call `retrieve_memory(session_id=<bound>, client_id=<bound>)` with no `memory_key` argument. This returns up to 20 of the most recent un-expired entries for this client.
2. Inspect the returned entries:
   - Keys starting with `client_pref:` are durable semantic preferences (e.g. tone, scoring weights, sourcing channels).
   - Keys starting with `consolidated:` are merged session summaries.
   - Keys starting with `eval_summary:` are recent candidate evaluations.
3. Summarize the salient context in 1-3 sentences. Prefer preferences over individual evaluation summaries unless the user specifically asked about a candidate.

## Gotchas

- This skill is read-only. Do not call `store_memory` from here — that belongs in `evaluate_candidate` after a successful evaluation.
- If `retrieve_memory` returns an empty list, tell the user there is no prior context for this client and continue the conversation normally.
- Memory entries from other clients are never returned — `retrieve_memory` is tenant-scoped by `client_id`. You do not need to filter the results yourself.
- Do not invent context that is not in the returned entries. If the user asks about something specific and it's not in memory, say so.
