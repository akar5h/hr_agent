"""Unified tracing decorator — stacks LangSmith, Langfuse, and Galileo
span decorators based on ENABLE_* env flags.

Usage::

    from src.observability.decorators import traced

    @traced(name="build-system-prompt")
    def build_system_prompt(...): ...

    @traced(name="check-input")
    async def check_input(...): ...
"""

from __future__ import annotations

import asyncio
import functools
import os
from typing import Any, Callable, Optional


# ── Lazy backend resolution (cached after first call) ──────────────

_backends_resolved = False
_traceable_fn: Optional[Callable] = None  # langsmith @traceable
_observe_fn: Optional[Callable] = None  # langfuse @observe
_galileo_log_fn: Optional[Callable] = None  # galileo @log


def _resolve_backends() -> None:
    """Import decorator factories from each enabled backend (once)."""
    global _backends_resolved, _traceable_fn, _observe_fn, _galileo_log_fn
    if _backends_resolved:
        return
    _backends_resolved = True

    # LangSmith
    if os.getenv("ENABLE_LANGSMITH", "false").strip().lower() == "true":
        try:
            from langsmith import traceable
            _traceable_fn = traceable
        except ImportError:
            pass

    # Langfuse
    if os.getenv("ENABLE_LANGFUSE", "false").strip().lower() == "true":
        try:
            from langfuse import observe
            _observe_fn = observe
        except ImportError:
            pass

    # Galileo
    if os.getenv("ENABLE_GALILEO", "false").strip().lower() == "true":
        try:
            from galileo import log as galileo_log
            _galileo_log_fn = galileo_log
        except ImportError:
            pass


def _build_chain(fn: Callable, name: str, span_type: str) -> Callable:
    """Stack enabled backend decorators around *fn* (innermost first)."""
    _resolve_backends()

    wrapped = fn

    # LangSmith (innermost — tightest LangChain integration)
    if _traceable_fn is not None:
        wrapped = _traceable_fn(name=name)(wrapped)

    # Langfuse
    if _observe_fn is not None:
        wrapped = _observe_fn(name=name)(wrapped)

    # Galileo (outermost)
    if _galileo_log_fn is not None:
        wrapped = _galileo_log_fn(span_type=span_type)(wrapped)

    return wrapped


# ── Public decorator ───────────────────────────────────────────────

def traced(
    name: str,
    *,
    span_type: str = "workflow",
) -> Callable:
    """Conditionally apply LangSmith ``@traceable``, Langfuse ``@observe``,
    and Galileo ``@log`` to a function based on ``ENABLE_*`` env flags.

    The decorator chain is built lazily on the first call and cached,
    so import-time cost is zero.
    """

    def decorator(fn: Callable) -> Callable:

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if async_wrapper._traced_inner is None:  # type: ignore[attr-defined]
                    async_wrapper._traced_inner = _build_chain(fn, name, span_type)  # type: ignore[attr-defined]
                return await async_wrapper._traced_inner(*args, **kwargs)  # type: ignore[attr-defined]

            async_wrapper._traced_inner = None  # type: ignore[attr-defined]
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                if sync_wrapper._traced_inner is None:  # type: ignore[attr-defined]
                    sync_wrapper._traced_inner = _build_chain(fn, name, span_type)  # type: ignore[attr-defined]
                return sync_wrapper._traced_inner(*args, **kwargs)  # type: ignore[attr-defined]

            sync_wrapper._traced_inner = None  # type: ignore[attr-defined]
            return sync_wrapper

    return decorator
