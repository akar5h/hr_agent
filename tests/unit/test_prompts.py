"""Compatibility prompt tests for Phase 3 verification command."""

from __future__ import annotations

import pytest

from src.prompts.evaluation import (
    STABLE_INSTRUCTIONS,
    build_system_prompt,
    build_system_prompt_blocks,
)


def test_system_prompt_contains_context_fields() -> None:
    prompt = build_system_prompt(client_id="client-123", session_id="session-abc")
    assert "client-123" in prompt
    assert "session-abc" in prompt
    assert "get_hiring_rubric" in prompt


def test_prompt_blocks_split_stable_and_dynamic() -> None:
    blocks = build_system_prompt_blocks(
        client_id="client-X",
        session_id="sess-1",
        model_name="anthropic/claude-3.5-haiku",
    )
    assert len(blocks) == 2
    stable, dynamic = blocks
    assert stable["type"] == "text"
    assert dynamic["type"] == "text"
    assert "expert HR recruitment agent" in stable["text"]
    assert "client-X" not in stable["text"]
    assert "client-X" in dynamic["text"]
    assert "sess-1" in dynamic["text"]


def test_prompt_blocks_cache_control_on_supported_model(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_PROMPT_CACHE", "true")
    blocks = build_system_prompt_blocks(
        client_id="c", session_id="s", model_name="anthropic/claude-3.5-haiku"
    )
    assert blocks[0].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]


def test_prompt_blocks_no_cache_control_for_unsupported_model() -> None:
    blocks = build_system_prompt_blocks(
        client_id="c", session_id="s", model_name="deepseek/deepseek-v3.2"
    )
    assert "cache_control" not in blocks[0]


def test_prompt_blocks_cache_control_off_when_env_disabled(monkeypatch) -> None:
    # prompt_cache_enabled() reads the env each call, so a monkeypatched env
    # alone is enough — no module reload needed.
    monkeypatch.setenv("ENABLE_PROMPT_CACHE", "false")
    blocks = build_system_prompt_blocks(
        client_id="c", session_id="s", model_name="anthropic/claude-3.5-haiku"
    )
    assert "cache_control" not in blocks[0]


def test_stable_instructions_do_not_carry_session_state() -> None:
    # The whole point of caching is that the stable block does not vary by
    # session or client. Smoke-test that no obvious dynamic placeholders leak.
    assert "Session ID" not in STABLE_INSTRUCTIONS
    assert "Client ID" not in STABLE_INSTRUCTIONS


def test_prompt_blocks_include_memory_in_dynamic_only() -> None:
    blocks = build_system_prompt_blocks(
        client_id="c",
        session_id="s",
        prior_memories=[{"memory_key": "pref:tone", "memory_value": "formal"}],
        model_name="anthropic/claude-3.5-haiku",
    )
    assert "pref:tone" not in blocks[0]["text"]
    assert "pref:tone" in blocks[1]["text"]
    assert "formal" in blocks[1]["text"]

