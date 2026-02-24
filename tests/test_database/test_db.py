"""Tests for src.database.db."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from src.database import db

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL required for Postgres database tests",
)


@pytest.fixture
def temp_db_path(monkeypatch: pytest.MonkeyPatch, tmp_path) -> str:
    """Provide an isolated database path for each test."""
    db_file = tmp_path / "nested" / "test.db"
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_file))
    return str(db_file)


def test_get_db_returns_connection(temp_db_path: str) -> None:
    with db.get_db() as conn:
        assert isinstance(conn, sqlite3.Connection)
        assert conn.row_factory is sqlite3.Row
        row = conn.execute("SELECT 1 AS value").fetchone()
        assert row["value"] == 1


def test_get_db_creates_directory(temp_db_path: str) -> None:
    with db.get_db():
        pass
    db_path = Path(db.get_db_path())
    assert db_path.parent.exists()
    assert db_path.exists()


def test_get_db_wal_mode(temp_db_path: str) -> None:
    with db.get_db() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(journal_mode).lower() == "wal"


def test_get_db_foreign_keys(temp_db_path: str) -> None:
    with db.get_db() as conn:
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert foreign_keys == 1


def test_get_db_commits_on_success(temp_db_path: str) -> None:
    with db.get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS sample (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO sample (name) VALUES (?)", ("saved",))

    with db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
    assert count == 1


def test_get_db_rollback_on_error(temp_db_path: str) -> None:
    with db.get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS sample (id INTEGER PRIMARY KEY, name TEXT)")

    with pytest.raises(RuntimeError):
        with db.get_db() as conn:
            conn.execute("INSERT INTO sample (name) VALUES (?)", ("rolled-back",))
            raise RuntimeError("trigger rollback")

    with db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
    assert count == 0
