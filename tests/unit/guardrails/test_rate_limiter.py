"""Unit tests for hardening rate limiter."""

from __future__ import annotations

import pytest

from src.guardrails import rate_limiter


def test_rate_limiter_noop_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(rate_limiter, "ENABLE_HARDENING", False)
    rate_limiter.record_tool_call("s1")
    assert rate_limiter.get_call_count("s1") == 0


def test_rate_limiter_raises_on_limit(monkeypatch) -> None:
    monkeypatch.setattr(rate_limiter, "ENABLE_HARDENING", True)
    monkeypatch.setattr(rate_limiter, "MAX_TOOL_CALLS", 1)
    rate_limiter.reset_session("s2")
    rate_limiter.record_tool_call("s2")
    with pytest.raises(rate_limiter.ToolRateLimitError):
        rate_limiter.record_tool_call("s2")
