"""Unit tests for token-aware compression helpers."""

from __future__ import annotations

from types import SimpleNamespace

from src.graph import compression


def test_count_tokens_approximate_returns_positive_value() -> None:
    assert compression.count_tokens_approximate("hello world") >= 1


def test_count_messages_tokens_sums_message_content() -> None:
    messages = [
        SimpleNamespace(content="abc"),
        SimpleNamespace(content=[{"text": "def"}, {"text": "ghi"}]),
    ]
    assert compression.count_messages_tokens(messages) >= 2


def test_compress_messages_token_aware_respects_threshold(monkeypatch) -> None:
    messages = [SimpleNamespace(content="x" * 200)]
    monkeypatch.setattr(compression, "TOKEN_COMPRESS_THRESHOLD", 1)
    monkeypatch.setattr(compression, "compress_messages", lambda msgs, _model: ["compressed"])
    result = compression.compress_messages_token_aware(messages, model=object())
    assert result == ["compressed"]
