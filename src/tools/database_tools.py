"""Database-backed tools for querying, writing, and rubric retrieval."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from src.database.db import get_db
from src.database.schema import CREATE_TABLES_SQL
from src.llm import DEFAULT_OPENROUTER_MODEL, build_chat_model
from src.tools._compat import tool

SQL_GENERATOR_MODEL = os.getenv("OPENROUTER_SQL_MODEL", DEFAULT_OPENROUTER_MODEL)


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


def _generate_sql(query_intent: str, client_id: str) -> str:
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
    return _clean_sql(_extract_text_from_model_response(response))


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
) -> dict:
    """Submit the final structured evaluation for a candidate.

    MUST be called as the last step of every candidate evaluation.
    Writes scores to the evaluations table and returns a confirmation.
    """
    try:
        with get_db() as conn:
            # Look up the candidate by name + client_id
            candidate_row = conn.execute(
                "SELECT id FROM candidates WHERE LOWER(name) = LOWER(%s) AND client_id = %s LIMIT 1",
                (candidate_name, client_id),
            ).fetchone()

            candidate_id = candidate_row["id"] if candidate_row else f"cand-{candidate_name.lower().replace(' ', '-')}"

            eval_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO evaluations
                    (id, candidate_id, position_id, client_id,
                     technical_score, experience_score, culture_score,
                     communication_score, overall_score, reasoning, recommendation)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
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
            "candidate_name": candidate_name,
            "overall_score": overall_score,
            "recommendation": recommendation,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


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
