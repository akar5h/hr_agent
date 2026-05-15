"""Unit tests for tool registry exports."""

from __future__ import annotations

from src.tools import ALL_TOOLS


def test_all_tools_contains_expected_tools() -> None:
    assert len(ALL_TOOLS) == 18


def test_load_skill_is_registered() -> None:
    names = {getattr(t, "name", "") for t in ALL_TOOLS}
    assert "load_skill" in names
