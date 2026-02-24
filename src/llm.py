"""LLM provider helpers for OpenRouter-backed chat models."""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-v3.2"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def build_chat_model(temperature: float = 0.0, model: str | None = None) -> ChatOpenAI:
    """Return a ChatOpenAI client configured for OpenRouter."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    model_name = model or os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
    base_url = os.getenv("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL)

    return ChatOpenAI(
        model=model_name,
        temperature=temperature,
        openai_api_key=api_key,
        openai_api_base=base_url,
    )
