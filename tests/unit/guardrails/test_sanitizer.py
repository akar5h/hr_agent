"""Unit tests for optional input sanitization."""

from __future__ import annotations

from src.guardrails import sanitizer


def test_sanitize_noop_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(sanitizer, "ENABLE_HARDENING", False)
    assert sanitizer.sanitize("abc\u200b") == "abc\u200b"


def test_sanitize_strips_zero_width_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(sanitizer, "ENABLE_HARDENING", True)
    assert sanitizer.sanitize("ab\u200bc") == "abc"


def test_add_instruction_boundary_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(sanitizer, "ENABLE_HARDENING", True)
    prompt = sanitizer.add_instruction_boundary("system prompt")
    assert sanitizer.INSTRUCTION_BOUNDARY in prompt
