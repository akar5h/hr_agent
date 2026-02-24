"""Unit tests for tool registry exports."""

from __future__ import annotations

from src.tools import ALL_TOOLS


def test_all_tools_contains_14_tools() -> None:
    assert len(ALL_TOOLS) == 14
