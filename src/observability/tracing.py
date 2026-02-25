"""LangSmith tracing configuration."""

from __future__ import annotations

import os


def configure_tracing() -> bool:
    """Enable LangSmith tracing if required env vars are present."""
    if os.getenv("LANGCHAIN_TRACING_V2", "false").lower() != "true":
        return False

    api_key = os.getenv("LANGCHAIN_API_KEY", "").strip()
    if not api_key:
        return False

    os.environ.setdefault("LANGCHAIN_PROJECT", os.getenv("LANGCHAIN_PROJECT", "hr-recruitment-agent"))
    return True
