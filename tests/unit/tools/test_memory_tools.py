"""Unit tests for memory persistence tools."""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

from src.database import db
from src.database.schema import init_db
from src.tools.memory_tools import retrieve_memory, store_memory
from tests.unit.tools.utils import invoke_tool

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL required for Postgres memory tool tests",
)


def _setup_db(monkeypatch, tmp_path) -> None:
    db_file = tmp_path / "memory.db"
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_file))
    init_db()


def test_store_memory_happy_path(monkeypatch, tmp_path) -> None:
    _setup_db(monkeypatch, tmp_path)
    result = invoke_tool(
        store_memory,
        session_id="session-1",
        client_id="client-techcorp",
        memory_key="note",
        memory_value="hello",
    )
    assert result == {"success": True, "memory_key": "note"}


def test_retrieve_memory_all(monkeypatch, tmp_path) -> None:
    _setup_db(monkeypatch, tmp_path)
    invoke_tool(
        store_memory,
        session_id="session-1",
        client_id="client-techcorp",
        memory_key="k1",
        memory_value="v1",
    )
    invoke_tool(
        store_memory,
        session_id="session-1",
        client_id="client-techcorp",
        memory_key="k2",
        memory_value="v2",
    )

    result = invoke_tool(
        retrieve_memory,
        session_id="session-1",
        client_id="client-techcorp",
        memory_key=None,
    )
    assert len(result) == 2
    assert {item["memory_key"] for item in result} == {"k1", "k2"}


def test_retrieve_memory_by_key(monkeypatch, tmp_path) -> None:
    _setup_db(monkeypatch, tmp_path)
    invoke_tool(
        store_memory,
        session_id="session-1",
        client_id="client-techcorp",
        memory_key="scoring_rule",
        memory_value="github_bonus=3.0",
    )

    result = invoke_tool(
        retrieve_memory,
        session_id="session-1",
        client_id="client-techcorp",
        memory_key="scoring_rule",
    )
    assert len(result) == 1
    assert result[0]["memory_value"] == "github_bonus=3.0"


def test_store_memory_handles_db_error(monkeypatch, tmp_path) -> None:
    _setup_db(monkeypatch, tmp_path)
    from src.tools import memory_tools

    @contextmanager
    def broken_db():
        raise RuntimeError("db unavailable")
        yield

    monkeypatch.setattr(memory_tools, "get_db", broken_db)
    result = invoke_tool(
        store_memory,
        session_id="session-1",
        client_id="client-techcorp",
        memory_key="k",
        memory_value="v",
    )
    assert result == {"success": False, "error": "db unavailable"}


def test_retrieve_memory_handles_db_error(monkeypatch, tmp_path) -> None:
    _setup_db(monkeypatch, tmp_path)
    from src.tools import memory_tools

    @contextmanager
    def broken_db():
        raise RuntimeError("db unavailable")
        yield

    monkeypatch.setattr(memory_tools, "get_db", broken_db)
    result = invoke_tool(
        retrieve_memory,
        session_id="session-1",
        client_id="client-techcorp",
        memory_key=None,
    )
    assert result == [{"error": "db unavailable"}]
