"""Tests for src.database.schema."""

from __future__ import annotations

import sqlite3

import pytest

from src.database import db
from src.database.schema import drop_all_tables, init_db

EXPECTED_TABLES = {
    "agent_memory",
    "candidates",
    "clients",
    "evaluations",
    "hiring_rubrics",
    "positions",
}


@pytest.fixture
def isolated_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    db_file = tmp_path / "schema-tests" / "schema.db"
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_file))


def _get_tables() -> set[str]:
    with db.get_db() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_init_db_creates_tables(isolated_db: None) -> None:
    init_db()
    tables = _get_tables()
    assert EXPECTED_TABLES.issubset(tables)


def test_drop_all_tables(isolated_db: None) -> None:
    init_db()
    drop_all_tables()
    tables = _get_tables()
    assert EXPECTED_TABLES.isdisjoint(tables)


def test_foreign_key_enforcement(isolated_db: None) -> None:
    init_db()
    with pytest.raises(sqlite3.IntegrityError):
        with db.get_db() as conn:
            conn.execute(
                (
                    "INSERT INTO positions (id, client_id, title, description, status) "
                    "VALUES (?, ?, ?, ?, ?)"
                ),
                ("pos-invalid", "client-does-not-exist", "Role", "Desc", "open"),
            )

