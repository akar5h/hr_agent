"""Shared test helpers for tool invocation."""

from __future__ import annotations

from typing import Any


def invoke_tool(tool_obj: Any, **kwargs: Any) -> Any:
    """Invoke either a plain function tool or a LangChain StructuredTool."""
    if hasattr(tool_obj, "invoke"):
        return tool_obj.invoke(kwargs)
    return tool_obj(**kwargs)

