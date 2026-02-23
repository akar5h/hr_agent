"""Compatibility prompt tests for Phase 3 verification command."""

from __future__ import annotations

from src.prompts.evaluation import build_system_prompt


def test_system_prompt_contains_context_fields() -> None:
    prompt = build_system_prompt(client_id="client-123", session_id="session-abc")
    assert "client-123" in prompt
    assert "session-abc" in prompt
    assert "get_hiring_rubric" in prompt

