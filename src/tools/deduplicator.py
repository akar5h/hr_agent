"""Candidate deduplication tool."""

from __future__ import annotations

from pydantic import BaseModel

from src.database.db import get_db
from src.tools._compat import tool


class DeduplicateCandidateInput(BaseModel):
    """Input schema for deduplicate_candidate."""

    email: str
    name: str
    client_id: str = ""


@tool(args_schema=DeduplicateCandidateInput)
def deduplicate_candidate(email: str, name: str, client_id: str = "") -> dict:
    """Check if candidate already exists. Returns existing record or signals new candidate."""
    del name  # Deliberately ignored per weak deduplication design.

    with get_db() as conn:
        if client_id:
            row = conn.execute(
                "SELECT id AS candidate_id, name FROM candidates WHERE email = %s AND client_id = %s",
                (email, client_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id AS candidate_id, name FROM candidates WHERE email = %s",
                (email,),
            ).fetchone()

    if row:
        return {"exists": True, "candidate_id": row["candidate_id"], "name": row["name"], "client_id": client_id}
    return {"exists": False}
