"""Tests for the per-turn session context contextvar."""

from __future__ import annotations

from src.guardrails.session_context import (
    get_session_client_id,
    get_session_id,
    session_scope,
)


def test_session_scope_sets_and_restores() -> None:
    assert get_session_client_id() is None
    with session_scope("client-A", "sess-1"):
        assert get_session_client_id() == "client-A"
        assert get_session_id() == "sess-1"
    assert get_session_client_id() is None
    assert get_session_id() is None


def test_session_scope_nesting_restores_outer() -> None:
    with session_scope("client-outer", "sess-outer"):
        assert get_session_client_id() == "client-outer"
        with session_scope("client-inner", "sess-inner"):
            assert get_session_client_id() == "client-inner"
            assert get_session_id() == "sess-inner"
        assert get_session_client_id() == "client-outer"
        assert get_session_id() == "sess-outer"


def test_empty_session_id_normalizes_to_none() -> None:
    with session_scope("client-only"):
        assert get_session_client_id() == "client-only"
        assert get_session_id() is None
