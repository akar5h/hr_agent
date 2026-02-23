"""Compatibility helpers for tool decorators."""

from __future__ import annotations

from typing import Any, Callable

try:
    from langchain.tools import tool as _lc_tool
except Exception:  # pragma: no cover - only used when langchain is unavailable
    try:
        from langchain_core.tools import tool as _lc_tool
    except Exception:  # pragma: no cover - only used when langchain_core is unavailable
        _lc_tool = None


def tool(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    """Return a LangChain tool decorator when available, otherwise a no-op."""
    if _lc_tool is not None:
        return _lc_tool(*args, **kwargs)

    # Bare decorator usage: @tool
    if args and callable(args[0]) and len(args) == 1 and not kwargs:
        func = args[0]
        setattr(func, "args_schema", None)
        return func

    # Keyword decorator usage: @tool(args_schema=...)
    def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, "args_schema", kwargs.get("args_schema"))
        return func

    return _decorator

