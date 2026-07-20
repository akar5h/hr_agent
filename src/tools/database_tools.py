"""Database-backed tools for querying, writing, and rubric retrieval."""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from src.cache.tool_cache import ToolCache
from src.database.db import get_db
from src.guardrails.session_context import get_session_client_id
from src.observability.decorators import traced
from src.database.schema import CREATE_TABLES_SQL
from src.llm import DEFAULT_SQLGEN_MODEL, build_chat_model
from src.tools._compat import tool

SQL_GENERATOR_MODEL = os.getenv("BEDROCK_SQLGEN_MODEL", DEFAULT_SQLGEN_MODEL)
_CACHE = ToolCache()
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BLOCKED_SCORE_WRITES: dict[str, set[str]] = {
    "evaluations": {
        "candidate_id",
        "position_id",
        "client_id",
        "technical_score",
        "experience_score",
        "culture_score",
        "communication_score",
        "overall_score",
        "reasoning",
        "recommendation",
    },
    "hiring_rubrics": {"criteria", "scoring_notes"},
    "candidates": {"score"},
}
# Tables that carry a client_id column and must be tenant-scoped on writes.
_CLIENT_SCOPED_TABLES: set[str] = {
    "positions",
    "hiring_rubrics",
    "candidates",
    "evaluations",
    "candidate_decisions",
    "outbound_emails",
    "agent_memory",
    "audit_events",
}


class QueryDatabaseInput(BaseModel):
    """Input schema for query_database."""

    query_intent: str
    client_id: str


class WriteDatabaseInput(BaseModel):
    """Input schema for write_database."""

    table: str
    operation: str
    data: dict[str, Any]
    where: dict[str, Any] = Field(default_factory=dict)


class GetHiringRubricInput(BaseModel):
    """Input schema for get_hiring_rubric."""

    position_id: str
    client_id: str


class GetCandidateByEmailInput(BaseModel):
    """Input schema for get_candidate_by_email."""

    email: str
    client_id: str


class GetExistingEvaluationInput(BaseModel):
    """Input schema for get_existing_evaluation."""

    position_id: str
    client_id: str
    candidate_id: str = ""
    candidate_name: str = ""


def _validate_identifier(name: str, kind: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid {kind}: {name!r}")


def _guard_write_database_request(
    table: str,
    data: dict[str, Any],
    where: dict[str, Any],
    *,
    session_client_id: str | None = None,
) -> None:
    """Keep generic writes from mutating scoring policy, score outputs, or other tenants."""
    _validate_identifier(table, "table")
    for column in [*data.keys(), *where.keys()]:
        _validate_identifier(str(column), "column")

    normalized_table = table.lower()
    changed_columns = {str(column).lower() for column in data.keys()}
    blocked_columns = _BLOCKED_SCORE_WRITES.get(normalized_table, set())
    attempted = sorted(changed_columns & blocked_columns)
    if attempted:
        if normalized_table == "evaluations":
            raise ValueError(
                "write_database cannot modify evaluation scoring records directly; "
                "use submit_evaluation so scoring is validated and idempotent."
            )
        if normalized_table == "hiring_rubrics":
            raise ValueError(
                "write_database cannot modify hiring rubric scoring policy directly; "
                "rubric updates need an explicit migration or admin workflow."
            )
        raise ValueError(
            "write_database cannot modify score fields directly; use the dedicated scoring workflow."
        )

    if normalized_table in _CLIENT_SCOPED_TABLES:
        if session_client_id:
            for source, payload in (("data", data), ("where", where)):
                if "client_id" in payload and str(payload["client_id"]) != session_client_id:
                    raise ValueError(
                        f"write_database refused: {source}.client_id "
                        f"({payload['client_id']!r}) does not match session client "
                        f"({session_client_id!r})."
                    )


def _extract_text_from_model_response(response: Any) -> str:
    content = getattr(response, "content", [])
    if isinstance(content, str):
        return content.strip()

    chunks: list[str] = []
    for block in content:
        text = None
        if isinstance(block, dict):
            text = block.get("text")
        else:
            text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()


def _clean_sql(sql: str) -> str:
    cleaned = sql.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("sql", "", 1).strip()
    return cleaned


@traced(name="generate-sql")
def _generate_sql(query_intent: str, client_id: str) -> str:
    cache_key = {
        "query_intent": query_intent.strip(),
        "client_id": client_id,
        "model": SQL_GENERATOR_MODEL,
    }
    cached = _CACHE.get("generate_sql", cache_key)
    if isinstance(cached, str) and cached.strip():
        return cached

    model = build_chat_model(temperature=0, model=SQL_GENERATOR_MODEL)
    system_prompt = (
        "You are a SQL generator. Generate only a SELECT query for PostgreSQL.\n"
        f"Schema:\n{CREATE_TABLES_SQL}\n"
        f"Client ID for filtering: {client_id}.\n"
        "Return only the SQL query, nothing else."
    )
    response = model.invoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query_intent},
        ]
    )
    generated = _clean_sql(_extract_text_from_model_response(response))
    _CACHE.set("generate_sql", cache_key, generated, ttl_seconds=900)
    return generated


@tool(args_schema=QueryDatabaseInput)
def query_database(query_intent: str, client_id: str) -> list[dict]:
    """Query the database using natural language. Converts to SQL and executes."""
    sql = ""
    try:
        sql = _generate_sql(query_intent, client_id)
        with get_db() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:
        return [{"error": str(exc), "generated_sql": sql}]


@tool(args_schema=WriteDatabaseInput)
def write_database(
    table: str,
    operation: str,
    data: dict[str, Any],
    where: Optional[dict[str, Any]] = None,
) -> dict:
    """Write or update records in the database."""
    where = where or {}

    try:
        _guard_write_database_request(
            table, data, where, session_client_id=get_session_client_id()
        )
        with get_db() as conn:
            if operation == "insert":
                columns = list(data.keys())
                placeholders = ", ".join(["%s"] * len(columns))
                sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
                cursor = conn.execute(sql, list(data.values()))
            elif operation == "update":
                set_clause = ", ".join(f"{col}=%s" for col in data.keys())
                where_clause = " AND ".join(f"{col}=%s" for col in where.keys())
                sql = f"UPDATE {table} SET {set_clause}"
                params = list(data.values())
                if where_clause:
                    sql = f"{sql} WHERE {where_clause}"
                    params.extend(where.values())
                cursor = conn.execute(sql, params)
            else:
                raise ValueError("operation must be either 'insert' or 'update'")

        return {"success": True, "rows_affected": cursor.rowcount}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


class SubmitEvaluationInput(BaseModel):
    """Input schema for submit_evaluation."""

    candidate_name: str
    position_id: str
    client_id: str
    technical_score: float = Field(..., ge=0.0, le=10.0)
    experience_score: float = Field(..., ge=0.0, le=10.0)
    culture_score: float = Field(..., ge=0.0, le=10.0)
    communication_score: float = Field(..., ge=0.0, le=10.0)
    overall_score: float = Field(..., ge=0.0, le=10.0)
    recommendation: Literal["STRONG_HIRE", "HIRE", "CONSIDER", "PASS"]
    reasoning: str
    session_id: str = ""


@tool(args_schema=SubmitEvaluationInput)
def submit_evaluation(
    candidate_name: str,
    position_id: str,
    client_id: str,
    technical_score: float,
    experience_score: float,
    culture_score: float,
    communication_score: float,
    overall_score: float,
    recommendation: str,
    reasoning: str,
    session_id: str = "",
) -> dict:
    """Submit the structured rubric scoring for a candidate.

    This is the last step of SCORING, not necessarily of the task. Writes scores to the
    evaluations table and returns a confirmation. If the user's goal is a decision
    (shortlist / reject / advance / decline), a submitted score alone does NOT commit
    the decision — follow this call with shortlist_candidate or reject_candidate.
    """
    try:
        with get_db() as conn:
            # Keep candidate identity inside the current client boundary.
            candidate_row = conn.execute(
                "SELECT id FROM candidates WHERE LOWER(name) = LOWER(%s) AND client_id = %s LIMIT 1",
                (candidate_name, client_id),
            ).fetchone()

            if candidate_row:
                candidate_id = candidate_row["id"]
            else:
                # Auto-create the candidate so the FK constraint is satisfied
                candidate_id = f"cand-{candidate_name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}"
                conn.execute(
                    "INSERT INTO candidates (id, name, client_id) VALUES (%s, %s, %s)",
                    (candidate_id, candidate_name, client_id),
                )

            idempotency_source = f"{client_id}:{position_id}:{candidate_id}:{session_id or 'default'}"
            eval_id = f"eval-{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_source).hex}"
            conn.execute(
                """
                INSERT INTO evaluations
                    (id, candidate_id, position_id, client_id,
                     technical_score, experience_score, culture_score,
                     communication_score, overall_score, reasoning, recommendation)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    technical_score = EXCLUDED.technical_score,
                    experience_score = EXCLUDED.experience_score,
                    culture_score = EXCLUDED.culture_score,
                    communication_score = EXCLUDED.communication_score,
                    overall_score = EXCLUDED.overall_score,
                    reasoning = EXCLUDED.reasoning,
                    recommendation = EXCLUDED.recommendation,
                    evaluated_at = CURRENT_TIMESTAMP
                """,
                (
                    eval_id, candidate_id, position_id, client_id,
                    technical_score, experience_score, culture_score,
                    communication_score, overall_score, reasoning, recommendation,
                ),
            )

        return {
            "success": True,
            "evaluation_id": eval_id,
            "candidate_id": candidate_id,
            "candidate_name": candidate_name,
            "overall_score": overall_score,
            "recommendation": recommendation,
            "idempotency_key": eval_id,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@tool(args_schema=GetCandidateByEmailInput)
def get_candidate_by_email(email: str, client_id: str) -> dict:
    """Fetch one candidate by email within the current client boundary."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id AS candidate_id, name, email, client_id
            FROM candidates
            WHERE email = %s AND client_id = %s
            LIMIT 1
            """,
            (email, client_id),
        ).fetchone()

    if row is None:
        return {"exists": False}
    return {"exists": True, **dict(row)}


@tool(args_schema=GetExistingEvaluationInput)
def get_existing_evaluation(
    position_id: str,
    client_id: str,
    candidate_id: str = "",
    candidate_name: str = "",
) -> dict:
    """Fetch the latest evaluation for a candidate/position within a client boundary."""
    if not candidate_id and not candidate_name:
        return {"exists": False, "error": "candidate_id or candidate_name is required"}

    params: list[Any] = [position_id, client_id]
    candidate_filter = "e.candidate_id = %s"
    if candidate_id:
        params.append(candidate_id)
    else:
        candidate_filter = "LOWER(c.name) = LOWER(%s)"
        params.append(candidate_name)

    with get_db() as conn:
        row = conn.execute(
            f"""
            SELECT
                e.id AS evaluation_id,
                e.candidate_id,
                c.name AS candidate_name,
                e.position_id,
                e.client_id,
                e.overall_score,
                e.recommendation,
                e.evaluated_at
            FROM evaluations e
            JOIN candidates c
              ON c.id = e.candidate_id
             AND c.client_id = e.client_id
            WHERE e.position_id = %s
              AND e.client_id = %s
              AND {candidate_filter}
            ORDER BY e.evaluated_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()

    if row is None:
        return {"exists": False}
    return {"exists": True, **dict(row)}


@tool(args_schema=GetHiringRubricInput)
def get_hiring_rubric(position_id: str, client_id: str) -> dict:
    """Get the hiring rubric for a position including scoring criteria and weights.

    Accepts either an exact `position_id` or a position title for convenience.
    """
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                hr.*,
                p.title AS position_title
            FROM hiring_rubrics hr
            JOIN positions p
              ON p.id = hr.position_id
             AND p.client_id = hr.client_id
            WHERE hr.client_id = %s
              AND (
                    hr.position_id = %s
                 OR LOWER(p.title) = LOWER(%s)
                 OR p.title ILIKE %s
              )
            ORDER BY
                CASE
                    WHEN hr.position_id = %s THEN 0
                    WHEN LOWER(p.title) = LOWER(%s) THEN 1
                    ELSE 2
                END,
                p.title ASC
            LIMIT 1
            """,
            (client_id, position_id, position_id, f"%{position_id}%", position_id, position_id),
        ).fetchone()

    if row is None:
        with get_db() as conn:
            positions = conn.execute(
                "SELECT id, title FROM positions WHERE client_id = %s ORDER BY title ASC",
                (client_id,),
            ).fetchall()
        return {
            "error": "Rubric not found",
            "hint": "Use a valid position_id (preferred) or an exact position title.",
            "available_positions": [{"id": p["id"], "title": p["title"]} for p in positions],
        }

    criteria = json.loads(row["criteria"]) if row["criteria"] else {}
    return {
        "rubric_id": row["id"],
        "position_id": row["position_id"],
        "position_title": row.get("position_title"),
        "client_id": row["client_id"],
        "criteria": criteria,
        "scoring_notes": row["scoring_notes"],
        "created_at": row["created_at"],
    }
