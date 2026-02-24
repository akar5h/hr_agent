"""Workflow action tools for shortlist/reject/email decisions from chat."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.database.db import get_db
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


@tool(args_schema=ShortlistCandidateInput)
def shortlist_candidate(
    candidate_id: str,
    position_id: str,
    client_id: str,
    reason: str = "",
    session_id: str = "",
) -> dict:
    """Mark candidate as shortlisted for a position."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM candidates WHERE id = %s LIMIT 1",
                (candidate_id,),
            ).fetchone()
            if row is None:
                return {"success": False, "error": "candidate_id not found"}

            inserted = conn.execute(
                """
                INSERT INTO candidate_decisions
                    (candidate_id, position_id, client_id, decision, reason, decided_by_session)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (candidate_id, position_id, client_id, "shortlist", reason, session_id),
            ).fetchone()

        return {
            "success": True,
            "decision_id": inserted["id"] if inserted else None,
            "candidate_id": candidate_id,
            "position_id": position_id,
            "decision": "shortlist",
            "reason": reason,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@tool(args_schema=RejectCandidateInput)
def reject_candidate(
    candidate_id: str,
    position_id: str,
    client_id: str,
    reason: str = "",
    session_id: str = "",
) -> dict:
    """Mark candidate as rejected for a position."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM candidates WHERE id = %s LIMIT 1",
                (candidate_id,),
            ).fetchone()
            if row is None:
                return {"success": False, "error": "candidate_id not found"}

            inserted = conn.execute(
                """
                INSERT INTO candidate_decisions
                    (candidate_id, position_id, client_id, decision, reason, decided_by_session)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (candidate_id, position_id, client_id, "reject", reason, session_id),
            ).fetchone()

        return {
            "success": True,
            "decision_id": inserted["id"] if inserted else None,
            "candidate_id": candidate_id,
            "position_id": position_id,
            "decision": "reject",
            "reason": reason,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@tool(args_schema=SendCandidateEmailInput)
def send_candidate_email(
    candidate_id: str,
    client_id: str,
    subject: str,
    body: str,
    session_id: str = "",
) -> dict:
    """Queue a candidate email (mock provider) from chat workflow."""
    try:
        with get_db() as conn:
            candidate = conn.execute(
                "SELECT id, email, name FROM candidates WHERE id = %s LIMIT 1",
                (candidate_id,),
            ).fetchone()
            if candidate is None:
                return {"success": False, "error": "candidate_id not found"}

            inserted = conn.execute(
                """
                INSERT INTO outbound_emails
                    (candidate_id, client_id, subject, body, status, provider, created_by_session)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (candidate_id, client_id, subject, body, "queued", "mock", session_id),
            ).fetchone()

        return {
            "success": True,
            "email_id": inserted["id"] if inserted else None,
            "candidate_id": candidate_id,
            "to_email": candidate["email"],
            "to_name": candidate["name"],
            "status": "queued",
            "provider": "mock",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
