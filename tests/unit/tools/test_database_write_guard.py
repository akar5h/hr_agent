"""Tests for write_database preflight guards that do not require a database."""

from __future__ import annotations

from src.tools.database_tools import write_database
from tests.unit.tools.utils import invoke_tool


def test_write_database_blocks_evaluation_score_updates_without_db() -> None:
    result = invoke_tool(
        write_database,
        table="evaluations",
        operation="update",
        data={"overall_score": 10.0},
        where={"id": "eval-existing"},
    )

    assert result["success"] is False
    assert "submit_evaluation" in result["error"]


def test_write_database_blocks_rubric_policy_updates_without_db() -> None:
    result = invoke_tool(
        write_database,
        table="hiring_rubrics",
        operation="update",
        data={"criteria": "{\"technical\": 100}"},
        where={"id": "rubric-techcorp-spe"},
    )

    assert result["success"] is False
    assert "rubric updates" in result["error"]


def test_write_database_rejects_unsafe_identifiers_without_db() -> None:
    result = invoke_tool(
        write_database,
        table="clients; DROP TABLE candidates",
        operation="update",
        data={"industry": "Tech"},
        where={"id": "client-techcorp"},
    )

    assert result["success"] is False
    assert "Invalid table" in result["error"]
