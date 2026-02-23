"""Database-backed tools for querying, writing, and rubric retrieval."""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.database.db import get_db
from src.database.schema import CREATE_TABLES_SQL
from src.tools._compat import tool

SQL_GENERATOR_MODEL = "claude-haiku-4-5-20251001"


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


def _extract_text_from_anthropic_response(response: Any) -> str:
    content = getattr(response, "content", [])
    if isinstance(content, str):
        return content.strip()

    chunks: list[str] = []
    for block in content:
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
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = (
        "You are a SQL generator. Generate only a SELECT query for SQLite.\n"
        f"Schema:\n{CREATE_TABLES_SQL}\n"
        f"Client ID for filtering: {client_id}.\n"
        "Return only the SQL query, nothing else."
    )
    response = client.messages.create(
        model=SQL_GENERATOR_MODEL,
        max_tokens=300,
        system=system_prompt,
        messages=[{"role": "user", "content": query_intent}],
    )
    return _clean_sql(_extract_text_from_anthropic_response(response))


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
                placeholders = ", ".join(["?"] * len(columns))
                sql = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
                cursor = conn.execute(sql, list(data.values()))
            elif operation == "update":
                set_clause = ", ".join(f"{col}=?" for col in data.keys())
                where_clause = " AND ".join(f"{col}=?" for col in where.keys())
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


@tool(args_schema=GetHiringRubricInput)
def get_hiring_rubric(position_id: str, client_id: str) -> dict:
    """Get the hiring rubric for a position including scoring criteria and weights."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM hiring_rubrics WHERE position_id = ? AND client_id = ?",
            (position_id, client_id),
        ).fetchone()

    if row is None:
        return {"error": "Rubric not found"}

    criteria = json.loads(row["criteria"]) if row["criteria"] else {}
    return {
        "rubric_id": row["id"],
        "position_id": row["position_id"],
        "client_id": row["client_id"],
        "criteria": criteria,
        "scoring_notes": row["scoring_notes"],
        "created_at": row["created_at"],
    }
