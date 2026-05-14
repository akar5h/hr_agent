"""Tests for per-tool rate limits and session context."""

from __future__ import annotations

import pytest

from src.guardrails import rate_limiter
from src.guardrails.rate_limiter import (
    DEFAULT_PER_TOOL_LIMITS,
    ToolRateLimitError,
    enforce_per_tool_limit,
    get_per_tool_count,
    record_tool_call,
    reset_session,
)


@pytest.fixture(autouse=True)
def _clear_counters():
    rate_limiter._per_tool_counts.clear()
    rate_limiter._session_counts.clear()
    yield
    rate_limiter._per_tool_counts.clear()
    rate_limiter._session_counts.clear()


def test_enforce_per_tool_limit_blocks_after_quota() -> None:
    limit = DEFAULT_PER_TOOL_LIMITS["send_candidate_email"]
    for _ in range(limit):
        enforce_per_tool_limit("sess-X", "send_candidate_email")
    with pytest.raises(ToolRateLimitError, match="send_candidate_email"):
        enforce_per_tool_limit("sess-X", "send_candidate_email")


def test_enforce_per_tool_limit_isolates_sessions() -> None:
    limit = DEFAULT_PER_TOOL_LIMITS["send_candidate_email"]
    for _ in range(limit):
        enforce_per_tool_limit("sess-A", "send_candidate_email")
    enforce_per_tool_limit("sess-B", "send_candidate_email")
    assert get_per_tool_count("sess-A", "send_candidate_email") == limit
    assert get_per_tool_count("sess-B", "send_candidate_email") == 1


def test_enforce_per_tool_limit_ignores_unbudgeted_tools() -> None:
    # No quota configured for parse_resume — should never raise.
    for _ in range(100):
        enforce_per_tool_limit("sess-unbudgeted", "parse_resume")
    assert get_per_tool_count("sess-unbudgeted", "parse_resume") == 0


def test_reset_session_clears_per_tool_counts() -> None:
    enforce_per_tool_limit("sess-reset", "shortlist_candidate")
    enforce_per_tool_limit("sess-reset", "reject_candidate")
    reset_session("sess-reset")
    assert get_per_tool_count("sess-reset", "shortlist_candidate") == 0
    assert get_per_tool_count("sess-reset", "reject_candidate") == 0


def test_record_tool_call_enforces_per_tool_when_hardening(monkeypatch) -> None:
    monkeypatch.setattr(rate_limiter, "ENABLE_HARDENING", True)
    monkeypatch.setitem(rate_limiter.DEFAULT_PER_TOOL_LIMITS, "send_candidate_email", 2)

    record_tool_call("sess-H", "send_candidate_email")
    record_tool_call("sess-H", "send_candidate_email")
    with pytest.raises(ToolRateLimitError):
        record_tool_call("sess-H", "send_candidate_email")


def test_record_tool_call_noop_when_hardening_off(monkeypatch) -> None:
    monkeypatch.setattr(rate_limiter, "ENABLE_HARDENING", False)
    # Should not enforce or raise.
    for _ in range(100):
        record_tool_call("sess-off", "send_candidate_email")
    assert get_per_tool_count("sess-off", "send_candidate_email") == 0
