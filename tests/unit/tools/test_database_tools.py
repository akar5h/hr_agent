"""Unit tests for database-backed tools."""

from __future__ import annotations

import os

import pytest

from src.database import db
from src.database.seed import run_seed
from src.tools.database_tools import get_hiring_rubric, query_database, write_database
from tests.unit.tools.utils import invoke_tool

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL required for Postgres database tool tests",
)


def _setup_seeded_db(monkeypatch, tmp_path) -> None:
    db_file = tmp_path / "phase2-tools.db"
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_file))
    run_seed()


def test_query_database_happy_path_with_mocked_sql(monkeypatch, tmp_path) -> None:
    _setup_seeded_db(monkeypatch, tmp_path)
    from src.tools import database_tools

    monkeypatch.setattr(
        database_tools,
        "_generate_sql",
        lambda query_intent, client_id: "SELECT id, name FROM candidates ORDER BY id LIMIT 1",
    )

    result = invoke_tool(
        query_database,
        query_intent="Find one candidate",
        client_id="client-techcorp",
    )
    assert len(result) == 1
    assert "id" in result[0]
    assert "name" in result[0]


def test_query_database_returns_error_on_sql_failure(monkeypatch, tmp_path) -> None:
    _setup_seeded_db(monkeypatch, tmp_path)
    from src.tools import database_tools

    monkeypatch.setattr(database_tools, "_generate_sql", lambda *_: "SELECT * FROM missing_table")
    result = invoke_tool(query_database, query_intent="anything", client_id="client-techcorp")
    assert "error" in result[0]
    assert result[0]["generated_sql"] == "SELECT * FROM missing_table"


def test_write_database_insert_happy_path(monkeypatch, tmp_path) -> None:
    _setup_seeded_db(monkeypatch, tmp_path)
    result = invoke_tool(
        write_database,
        table="agent_memory",
        operation="insert",
        data={
            "session_id": "session-1",
            "client_id": "client-techcorp",
            "memory_key": "scoring_rule",
            "memory_value": "github_bonus=3.0",
        },
        where={},
    )
    assert result["success"] is True
    assert result["rows_affected"] == 1


def test_write_database_update_happy_path(monkeypatch, tmp_path) -> None:
    _setup_seeded_db(monkeypatch, tmp_path)
    result = invoke_tool(
        write_database,
        table="clients",
        operation="update",
        data={"industry": "Tech"},
        where={"id": "client-techcorp"},
    )
    assert result["success"] is True
    assert result["rows_affected"] == 1

    with db.get_db() as conn:
        row = conn.execute(
            "SELECT industry FROM clients WHERE id = %s",
            ("client-techcorp",),
        ).fetchone()
    assert row["industry"] == "Tech"


def test_write_database_invalid_operation(monkeypatch, tmp_path) -> None:
    _setup_seeded_db(monkeypatch, tmp_path)
    result = invoke_tool(
        write_database,
        table="clients",
        operation="delete",
        data={"name": "X"},
        where={"id": "client-techcorp"},
    )
    assert result["success"] is False
    assert "operation must be either" in result["error"]


def test_get_hiring_rubric_happy_path(monkeypatch, tmp_path) -> None:
    _setup_seeded_db(monkeypatch, tmp_path)
    result = invoke_tool(
        get_hiring_rubric,
        position_id="pos-techcorp-spe",
        client_id="client-techcorp",
    )
    assert result["rubric_id"] == "rubric-techcorp-spe"
    assert result["criteria"]["technical"] == 40


def test_get_hiring_rubric_not_found(monkeypatch, tmp_path) -> None:
    _setup_seeded_db(monkeypatch, tmp_path)
    result = invoke_tool(
        get_hiring_rubric,
        position_id="missing-position",
        client_id="client-techcorp",
    )
    assert result["error"] == "Rubric not found"
    assert "available_positions" in result


def test_get_hiring_rubric_resolves_by_position_title(monkeypatch, tmp_path) -> None:
    _setup_seeded_db(monkeypatch, tmp_path)
    result = invoke_tool(
        get_hiring_rubric,
        position_id="Senior Python Engineer",
        client_id="client-techcorp",
    )
    assert result["rubric_id"] == "rubric-techcorp-spe"
    assert result["position_id"] == "pos-techcorp-spe"
