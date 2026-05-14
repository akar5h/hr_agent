"""Tests for tenant/client boundary enforcement in write_database."""

from __future__ import annotations

import pytest

from src.guardrails.session_context import session_scope
from src.tools.database_tools import _guard_write_database_request, write_database
from tests.unit.tools.utils import invoke_tool


def test_guard_blocks_cross_tenant_data_client_id() -> None:
    with pytest.raises(ValueError, match="does not match session client"):
        _guard_write_database_request(
            "candidates",
            data={"id": "cand-x", "name": "X", "client_id": "client-attacker"},
            where={},
            session_client_id="client-victim",
        )


def test_guard_blocks_cross_tenant_where_client_id() -> None:
    with pytest.raises(ValueError, match="does not match session client"):
        _guard_write_database_request(
            "candidates",
            data={"name": "renamed"},
            where={"id": "cand-x", "client_id": "client-attacker"},
            session_client_id="client-victim",
        )


def test_guard_allows_same_tenant() -> None:
    # Should not raise.
    _guard_write_database_request(
        "candidates",
        data={"id": "cand-x", "name": "X", "client_id": "client-victim"},
        where={},
        session_client_id="client-victim",
    )


def test_guard_passes_when_no_session_client_bound() -> None:
    # Tools invoked outside of a session_scope (e.g. CLI scripts) must still
    # work; the boundary check only fires when a session client is in scope.
    _guard_write_database_request(
        "candidates",
        data={"id": "cand-x", "name": "X", "client_id": "client-anything"},
        where={},
        session_client_id=None,
    )


def test_guard_skips_boundary_check_for_unscoped_tables() -> None:
    # `clients` table itself has no client_id column and is not in the
    # scoped-table set, so a write of any payload should pass the guard.
    _guard_write_database_request(
        "clients",
        data={"id": "client-X", "name": "X", "industry": "Tech"},
        where={},
        session_client_id="client-victim",
    )


def test_write_database_returns_error_on_boundary_violation() -> None:
    with session_scope("client-victim", "sess-1"):
        result = invoke_tool(
            write_database,
            table="candidates",
            operation="update",
            data={"name": "renamed"},
            where={"id": "cand-x", "client_id": "client-attacker"},
        )
    assert result["success"] is False
    assert "does not match session client" in result["error"]


def test_score_creep_guard_still_fires_for_evaluations() -> None:
    with pytest.raises(ValueError, match="submit_evaluation"):
        _guard_write_database_request(
            "evaluations",
            data={"overall_score": 10.0},
            where={"id": "eval-x"},
            session_client_id="client-victim",
        )
