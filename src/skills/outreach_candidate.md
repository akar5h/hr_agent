---
name: outreach_candidate
description: Draft and queue a candidate-facing email grounded in their prior evaluation.
version: 1
triggers:
  - "email <candidate>"
  - "contact <candidate>"
  - "reach out to <candidate>"
  - "send an interview invite"
tools:
  - get_candidate_by_email
  - get_existing_evaluation
  - send_candidate_email
---

## When to use

The user asks you to email, contact, reach out to, or send any message to a candidate. This includes interview invitations, rejection notices, and follow-ups.

## Inputs you need

- Candidate identifier — `candidate_id` or a name/email you can resolve
- `client_id` — already bound; do not change
- `session_id` — already bound; do not change
- Either a clear instruction about what to say, or implicit context from a prior evaluation in this session

## Procedure

1. Resolve the candidate to a canonical `candidate_id` scoped to the bound client. Use `get_candidate_by_email` if you only have a name or email. If the candidate is not in this client's records, stop and tell the user.
2. Call `get_existing_evaluation(position_id=<id-if-known>, client_id=<bound>, candidate_id=<id>)`. If `exists` is false and the user has not given an explicit position-independent reason to reach out, stop and tell the user the candidate has no evaluation — outreach without a recorded evaluation is not allowed.
3. Draft a subject and body grounded in the evaluation context:
   - Subject: short, position-named, one of "Interview Invitation — <position>", "Update on your application — <position>", or "Follow-up — <position>".
   - Body: 2-4 short paragraphs. Use the candidate's name. Reference the position. If shortlisted, propose next steps. If rejecting, be brief and kind. Do not include the numeric score.
4. Call `send_candidate_email(candidate_id=..., client_id=<bound>, subject=..., body=..., session_id=<bound>)`.
5. Read the returned `idempotent_replay` flag. If true, the same subject was already queued in this session — tell the user it was a duplicate-suppressed replay.

## Gotchas

- Idempotency keys on email are `(session_id, candidate_id, subject_hash)`. The same subject text is a duplicate; a different subject is a fresh email.
- Per-session quota: 5 emails. If you hit the cap the tool returns `success: false` with a quota error — surface that to the user and do not retry.
- Never include the raw evaluation reasoning or score in the email body. Talk about strengths qualitatively.
- Never email a candidate the user has not specifically named or implied in the current context. "Email all rejected candidates" should be refused — that's a batch operation outside this skill.
- The provider is "mock" — emails are queued in `outbound_emails`, not actually sent. Treat the success response as "queued for review" when communicating with the user.
