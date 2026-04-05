"""Unified tracing layer — LangSmith, Langfuse, and Galileo AI.

Each backend is toggled independently via env flags:
  ENABLE_LANGSMITH=true/false
  ENABLE_LANGFUSE=true/false
  ENABLE_GALILEO=true/false

When a backend is enabled AND its credentials are present, a callback
handler is created and returned by ``get_trace_callbacks()``.

Per-function tracing is handled by ``src.observability.decorators.traced``.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from src.observability.logging import get_logger

log = get_logger(__name__)


# ── helpers ────────────────────────────────────────────────────────────
def _is_enabled(flag_name: str) -> bool:
    return os.getenv(flag_name, "false").strip().lower() == "true"


def _has_key(*env_names: str) -> bool:
    return all(os.getenv(n, "").strip() for n in env_names)


# ── LangSmith ─────────────────────────────────────────────────────────
def _configure_langsmith() -> bool:
    """Enable LangSmith auto-tracing via env vars."""
    if not _is_enabled("ENABLE_LANGSMITH"):
        return False
    # Support both old (LANGCHAIN_*) and new (LANGSMITH_*) naming
    api_key = (
        os.getenv("LANGSMITH_API_KEY", "").strip()
        or os.getenv("LANGCHAIN_API_KEY", "").strip()
    )
    if not api_key:
        log.info("langsmith: skipped (no API key)")
        return False

    # LangSmith auto-instruments when these env vars are set.
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ.setdefault("LANGCHAIN_API_KEY", api_key)
    os.environ.setdefault("LANGSMITH_API_KEY", api_key)
    os.environ.setdefault(
        "LANGCHAIN_PROJECT",
        os.getenv("LANGSMITH_PROJECT", "hr-recruitment-agent"),
    )
    os.environ.setdefault(
        "LANGCHAIN_ENDPOINT",
        os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
    )
    log.info("langsmith: enabled (project=%s)", os.environ["LANGCHAIN_PROJECT"])
    return True


# ── Langfuse ──────────────────────────────────────────────────────────
def _configure_langfuse() -> bool:
    """Verify Langfuse SDK + credentials are available."""
    if not _is_enabled("ENABLE_LANGFUSE"):
        return False
    if not _has_key("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"):
        log.info("langfuse: skipped (missing keys)")
        return False
    try:
        from langfuse.langchain import CallbackHandler  # noqa: F401
        log.info("langfuse: enabled (host=%s)", os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"))
        return True
    except ImportError:
        log.warning("langfuse: skipped (package not installed)")
        return False


def _get_langfuse_handler() -> Any | None:
    """Create a plain Langfuse CallbackHandler (v4 API).

    In Langfuse v4, session_id/user_id/tags are passed via config metadata,
    not via the constructor.
    """
    if not _is_enabled("ENABLE_LANGFUSE"):
        return None
    if not _has_key("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"):
        return None
    try:
        from langfuse.langchain import CallbackHandler
        return CallbackHandler()
    except ImportError:
        return None


def _build_langfuse_metadata(
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    trace_name: Optional[str] = None,
) -> dict[str, Any]:
    """Build Langfuse v4 metadata dict for config["metadata"]."""
    meta: dict[str, Any] = {}
    if session_id is not None:
        meta["langfuse_session_id"] = session_id
    if user_id is not None:
        meta["langfuse_user_id"] = user_id
    if tags is not None:
        meta["langfuse_tags"] = tags
    if trace_name is not None:
        meta["langfuse_trace_name"] = trace_name
    return meta


# ── Galileo AI ────────────────────────────────────────────────────────
def _configure_galileo() -> bool:
    """Verify Galileo SDK + credentials are available."""
    if not _is_enabled("ENABLE_GALILEO"):
        return False
    if not _has_key("GALILEO_API_KEY"):
        log.info("galileo: skipped (no API key)")
        return False
    try:
        from galileo import galileo_context  # noqa: F401
        project = os.getenv("GALILEO_PROJECT", "hr-recruitment-agent")
        log_stream = os.getenv("GALILEO_LOG_STREAM", "default")
        galileo_context.init(project=project, log_stream=log_stream)
        log.info("galileo: enabled (project=%s, stream=%s)", project, log_stream)
        return True
    except ImportError:
        log.warning("galileo: skipped (package not installed)")
        return False


def _get_galileo_handler() -> Any | None:
    if not _is_enabled("ENABLE_GALILEO"):
        return None
    if not _has_key("GALILEO_API_KEY"):
        return None
    try:
        from galileo.handlers.langchain import GalileoCallback
        return GalileoCallback(flush_on_chain_end=True)
    except ImportError:
        return None


# ── Public API ────────────────────────────────────────────────────────
def configure_tracing() -> dict[str, bool]:
    """Initialise all enabled tracing backends.

    Returns a dict showing which backends were activated, e.g.
    ``{"langsmith": True, "langfuse": True, "galileo": False}``
    """
    status = {
        "langsmith": _configure_langsmith(),
        "langfuse": _configure_langfuse(),
        "galileo": _configure_galileo(),
    }
    active = [k for k, v in status.items() if v]
    log.info("tracing: backends=%s", active or "none")
    return status


def get_trace_config(
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    trace_name: Optional[str] = None,
) -> dict[str, Any]:
    """Build a complete LangChain config dict with callbacks + metadata.

    Returns a dict like::

        {
            "callbacks": [langfuse_handler, galileo_handler],
            "metadata": {"langfuse_session_id": "...", ...},
        }

    Merge this into your thread_config before calling ``.invoke()``/``.stream()``.
    """
    callbacks: list[Any] = []

    langfuse_cb = _get_langfuse_handler()
    if langfuse_cb is not None:
        callbacks.append(langfuse_cb)

    galileo_cb = _get_galileo_handler()
    if galileo_cb is not None:
        callbacks.append(galileo_cb)

    metadata = _build_langfuse_metadata(
        session_id=session_id,
        user_id=user_id,
        tags=tags,
        trace_name=trace_name,
    )

    result: dict[str, Any] = {}
    if callbacks:
        result["callbacks"] = callbacks
    if metadata:
        result["metadata"] = metadata
    return result


# ── Backward-compat aliases ───────────────────────────────────────────
def get_trace_callbacks(
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    trace_name: Optional[str] = None,
) -> list[Any]:
    """Deprecated — prefer ``get_trace_config()`` for full metadata support."""
    config = get_trace_config(
        session_id=session_id,
        user_id=user_id,
        tags=tags,
        trace_name=trace_name,
    )
    return config.get("callbacks", [])


def get_langfuse_handler(
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    trace_name: Optional[str] = None,
) -> Any | None:
    """Deprecated — prefer ``get_trace_config()`` for full metadata support."""
    return _get_langfuse_handler()
