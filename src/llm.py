"""LLM provider helpers for OpenRouter-backed chat models."""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

from src.observability.decorators import traced

DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-v3.2"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_FALLBACK_MODEL = "deepseek/deepseek-chat"


@traced(name="build-chat-model")
def build_chat_model(temperature: float = 0.0, model: str | None = None) -> ChatOpenAI:
    """Return a ChatOpenAI client configured for OpenRouter with fallback."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    model_name = model or os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
    fallback_model_name = os.getenv(
        "OPENROUTER_FALLBACK_MODEL",
        DEFAULT_OPENROUTER_FALLBACK_MODEL,
    )
    base_url = os.getenv("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL)

    primary = ChatOpenAI(
        model=model_name,
        temperature=temperature,
        openai_api_key=api_key,
        openai_api_base=base_url,
    )

    fallback = ChatOpenAI(
        model=fallback_model_name,
        temperature=temperature,
        openai_api_key=api_key,
        openai_api_base=base_url,
    )
    return primary.with_fallbacks([fallback])
