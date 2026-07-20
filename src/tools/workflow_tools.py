"""Workflow action tools for shortlist/reject/email decisions from chat."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field

from src.database.db import get_db
from src.guardrails.audit import record_audit_event
from src.guardrails.rate_limiter import ToolRateLimitError, enforce_per_tool_limit
from src.tools._compat import tool


class ShortlistCandidateInput(BaseModel):
    candidate_id: str
    position_id: str
    client_id: str
    reason: str = Field(default="")
    session_id: str = Field(default="")


class RejectCandidateInput(BaseModel):
    candidate_id: str
    position_id: str
    client_id: str
    reason: str = Field(default="")
    session_id: str = Field(default="")


class SendCandidateEmailInput(BaseModel):
    candidate_id: str
    client_id: str
    subject: str
    body: str
    session_id: str = Field(default="")


def _idempotency_key(*parts: str) -> str:
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _lookup_candidate_in_client(candidate_id: str, client_id: str) -> dict[str, Any] | None:
    """Fetch a candidate scoped to the calling client, or None if not in this tenant."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, name, email, client_id
            FROM candidates
            WHERE id = %s AND client_id = %s
            LIMIT 1
            """,
            (candidate_id, client_id),
        ).fetchone()
    return dict(row) if row else None


def _record_decision(
    *,
    candidate_id: str,
    position_id: str,
    client_id: str,
    decision: str,
    reason: str,
    session_id: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Idempotent insert into candidate_decisions.

    Returns (row, was_new). Uses an idempotency key on
    (action, session_id, candidate_id, position_id) and relies on the unique
    index added in migration 0004. On conflict the existing row is returned.
    """
    key = _idempotency_key("decision", decision, session_id or "default", candidate_id, position_id)
    with get_db() as conn:
        result = conn.execute(
            """
            INSERT INTO candidate_decisions
                (candidate_id, position_id, client_id, decision, reason,
                 decided_by_session, idempotency_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
            RETURNING id, decision, reason, decided_at
            """,
            (candidate_id, position_id, client_id, decision, reason, session_id, key),
        ).fetchone()

        if result is not None:
            return dict(result), True

        existing = conn.execute(
            """
            SELECT id, decision, reason, decided_at
            FROM candidate_decisions
            WHERE idempotency_key = %s
            LIMIT 1
            """,
            (key,),
        ).fetchone()
    return (dict(existing) if existing else None), False


def _record_email(
    *,
    candidate_id: str,
    client_id: str,
    subject: str,
    body: str,
    session_id: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Idempotent insert into outbound_emails keyed on (session, candidate, subject hash)."""
    subject_hash = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:16]
    key = _idempotency_key("email", session_id or "default", candidate_id, subject_hash)
    with get_db() as conn:
        result = conn.execute(
            """
            INSERT INTO outbound_emails
                (candidate_id, client_id, subject, body, status, provider,
                 created_by_session, idempotency_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
            RETURNING id, subject, status, created_at
            """,
            (candidate_id, client_id, subject, body, "queued", "mock", session_id, key),
        ).fetchone()

        if result is not None:
            return dict(result), True

        existing = conn.execute(
            """
            SELECT id, subject, status, created_at
            FROM outbound_emails
            WHERE idempotency_key = %s
            LIMIT 1
            """,
            (key,),
        ).fetchone()
    return (dict(existing) if existing else None), False


@tool(args_schema=ShortlistCandidateInput)
def shortlist_candidate(
    candidate_id: str,
    position_id: str,
    client_id: str,
    reason: str = "",
    session_id: str = "",
) -> dict:
    """Mark candidate as shortlisted for a position (idempotent, client-scoped)."""
    try:
        enforce_per_tool_limit(session_id or "default", "shortlist_candidate")
    except ToolRateLimitError as exc:
        return {"success": False, "error": str(exc)}

    try:
        candidate = _lookup_candidate_in_client(candidate_id, client_id)
        if candidate is None:
            return {"success": False, "error": "candidate_id not found for this client"}

        row, was_new = _record_decision(
            candidate_id=candidate_id,
            position_id=position_id,
            client_id=client_id,
            decision="shortlist",
            reason=reason,
            session_id=session_id,
        )

        result = {
            "success": True,
            "decision_id": row["id"] if row else None,
            "candidate_id": candidate_id,
            "position_id": position_id,
            "decision": "shortlist",
            "reason": reason,
            "idempotent_replay": not was_new,
        }
        record_audit_event(
            client_id=client_id,
            tool="shortlist_candidate",
            action="shortlist",
            session_id=session_id,
            target_id=candidate_id,
            payload={"position_id": position_id, "reason": reason},
            outcome="ok" if was_new else "duplicate",
        )
        return result
    except Exception as exc:
        record_audit_event(
            client_id=client_id,
            tool="shortlist_candidate",
            action="shortlist",
            session_id=session_id,
            target_id=candidate_id,
            payload={"position_id": position_id},
            outcome="error",
            error=str(exc),
        )
        return {"success": False, "error": str(exc)}


@tool(args_schema=RejectCandidateInput)
def reject_candidate(
    candidate_id: str,
    position_id: str,
    client_id: str,
    reason: str = "",
    session_id: str = "",
) -> dict:
    """Mark candidate as rejected for a position (idempotent, client-scoped)."""
    try:
        enforce_per_tool_limit(session_id or "default", "reject_candidate")
    except ToolRateLimitError as exc:
        return {"success": False, "error": str(exc)}

    try:
        candidate = _lookup_candidate_in_client(candidate_id, client_id)
        if candidate is None:
            return {"success": False, "error": "candidate_id not found for this client"}

        row, was_new = _record_decision(
            candidate_id=candidate_id,
            position_id=position_id,
            client_id=client_id,
            decision="reject",
            reason=reason,
            session_id=session_id,
        )

        result = {
            "success": True,
            "decision_id": row["id"] if row else None,
            "candidate_id": candidate_id,
            "position_id": position_id,
            "decision": "reject",
            "reason": reason,
            "idempotent_replay": not was_new,
        }
        record_audit_event(
            client_id=client_id,
            tool="reject_candidate",
            action="reject",
            session_id=session_id,
            target_id=candidate_id,
            payload={"position_id": position_id, "reason": reason},
            outcome="ok" if was_new else "duplicate",
        )
        return result
    except Exception as exc:
        record_audit_event(
            client_id=client_id,
            tool="reject_candidate",
            action="reject",
            session_id=session_id,
            target_id=candidate_id,
            payload={"position_id": position_id},
            outcome="error",
            error=str(exc),
        )
        return {"success": False, "error": str(exc)}


@tool(args_schema=SendCandidateEmailInput)
def send_candidate_email(
    candidate_id: str,
    client_id: str,
    subject: str,
    body: str,
    session_id: str = "",
) -> dict:
    """Queue a candidate email (mock provider, idempotent, client-scoped)."""
    try:
        enforce_per_tool_limit(session_id or "default", "send_candidate_email")
    except ToolRateLimitError as exc:
        return {"success": False, "error": str(exc)}

    try:
        candidate = _lookup_candidate_in_client(candidate_id, client_id)
        if candidate is None:
            return {"success": False, "error": "candidate_id not found for this client"}

        row, was_new = _record_email(
            candidate_id=candidate_id,
            client_id=client_id,
            subject=subject,
            body=body,
            session_id=session_id,
        )

        result = {
            "success": True,
            "email_id": row["id"] if row else None,
            "candidate_id": candidate_id,
            "to_email": candidate["email"],
            "to_name": candidate["name"],
            "status": row["status"] if row else "queued",
            "provider": "mock",
            "idempotent_replay": not was_new,
        }
        record_audit_event(
            client_id=client_id,
            tool="send_candidate_email",
            action="email_queued",
            session_id=session_id,
            target_id=candidate_id,
            payload={"subject": subject},
            outcome="ok" if was_new else "duplicate",
        )
        return result
    except Exception as exc:
        record_audit_event(
            client_id=client_id,
            tool="send_candidate_email",
            action="email_queued",
            session_id=session_id,
            target_id=candidate_id,
            payload={"subject": subject},
            outcome="error",
            error=str(exc),
        )
        return {"success": False, "error": str(exc)}
