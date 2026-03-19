"""Tracing configuration for LangSmith and Langfuse."""

from __future__ import annotations

import os
from typing import Optional


def configure_tracing() -> bool:
    """Enable tracing backends. Returns True if any backend was configured."""
    langsmith_ok = _configure_langsmith()
    langfuse_ok = _configure_langfuse()
    return langsmith_ok or langfuse_ok


def _configure_langsmith() -> bool:
    """Enable LangSmith tracing if required env vars are present."""
    if os.getenv("LANGCHAIN_TRACING_V2", "false").lower() != "true":
        return False

    api_key = os.getenv("LANGCHAIN_API_KEY", "").strip()
    if not api_key:
        return False

    os.environ.setdefault(
        "LANGCHAIN_PROJECT",
        os.getenv("LANGCHAIN_PROJECT", "hr-recruitment-agent"),
    )
    return True


def _configure_langfuse() -> bool:
    """Check that Langfuse env vars are present.

    Langfuse SDK v4 reads LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, and
    LANGFUSE_BASE_URL from the environment automatically — no manual
    client init required.
    """
    secret = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    public = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    if not secret or not public:
        return False

    try:
        from langfuse.langchain import CallbackHandler  # noqa: F401

        return True
    except ImportError:
        return False


def get_langfuse_handler(
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    trace_name: Optional[str] = None,
):
    """Create a Langfuse CallbackHandler for a single invocation.

    Pass the returned handler via ``config={"callbacks": [handler]}``
    on any LangChain/LangGraph ``.invoke()`` / ``.stream()`` call.

    Args:
        session_id: Groups traces into a conversation/session.
        user_id: Associates the trace with a specific user.
        tags: Filterable tags (e.g. ["hr-agent", "evaluation"]).
        trace_name: Descriptive name shown in the Langfuse UI.

    Returns:
        A ``CallbackHandler`` instance, or ``None`` if Langfuse is not
        configured (missing env vars or package not installed).
    """
    secret = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    public = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    if not secret or not public:
        return None

    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:
        return None

    kwargs: dict = {}
    if session_id is not None:
        kwargs["session_id"] = session_id
    if user_id is not None:
        kwargs["user_id"] = user_id
    if tags is not None:
        kwargs["tags"] = tags
    if trace_name is not None:
        kwargs["trace_name"] = trace_name

    return CallbackHandler(**kwargs)
