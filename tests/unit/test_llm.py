"""Unit tests for the LLM provider helpers."""

from __future__ import annotations

import pytest

import src.llm as llm


def test_default_primary_is_claude_haiku() -> None:
    assert llm.DEFAULT_OPENROUTER_MODEL == "anthropic/claude-3.5-haiku"


def test_default_fallback_is_deepseek() -> None:
    assert llm.DEFAULT_OPENROUTER_FALLBACK_MODEL == "deepseek/deepseek-v3.2"


def test_prompt_cache_enabled_defaults_true(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_PROMPT_CACHE", raising=False)
    assert llm.prompt_cache_enabled() is True


def test_prompt_cache_enabled_respects_env_off(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_PROMPT_CACHE", "false")
    assert llm.prompt_cache_enabled() is False


@pytest.mark.parametrize(
    "model_name,expected",
    [
        ("anthropic/claude-3.5-haiku", True),
        ("anthropic/claude-sonnet-4.5", True),
        ("deepseek/deepseek-v3.2", False),
        ("openai/gpt-4o-mini", False),
    ],
)
def test_model_supports_cache_control(model_name: str, expected: bool) -> None:
    assert llm._model_supports_cache_control(model_name) is expected


def test_build_chat_model_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        llm.build_chat_model()
