"""Unit tests for candidate deduplication tool."""

from __future__ import annotations

from src.database import db
from src.database.seed import run_seed
from src.tools.deduplicator import deduplicate_candidate
from tests.unit.tools.utils import invoke_tool


def _setup_seeded_db(monkeypatch, tmp_path) -> None:
    db_file = tmp_path / "dedup.db"
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_file))
    run_seed()


def test_deduplicate_candidate_found_by_email(monkeypatch, tmp_path) -> None:
    _setup_seeded_db(monkeypatch, tmp_path)
    result = invoke_tool(
        deduplicate_candidate,
        email="alice.chen@email.com",
        name="Alice Chen",
    )
    assert result["exists"] is True
    assert result["candidate_id"] == "cand-alice-chen"


def test_deduplicate_candidate_returns_false_for_new_email(monkeypatch, tmp_path) -> None:
    _setup_seeded_db(monkeypatch, tmp_path)
    result = invoke_tool(
        deduplicate_candidate,
        email="new.person@email.com",
        name="New Person",
    )
    assert result == {"exists": False}


def test_deduplicate_candidate_ignores_name(monkeypatch, tmp_path) -> None:
    _setup_seeded_db(monkeypatch, tmp_path)
    result = invoke_tool(
        deduplicate_candidate,
        email="alice.chen@email.com",
        name="Completely Different Name",
    )
    assert result["exists"] is True
    assert result["name"] == "Alice Chen"

