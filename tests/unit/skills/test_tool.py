"""Tests for the load_skill tool."""

from __future__ import annotations

from src.skills.tool import load_skill
from tests.unit.tools.utils import invoke_tool


def test_load_skill_returns_body_for_known_skill() -> None:
    result = invoke_tool(load_skill, name="evaluate_candidate")
    assert result["name"] == "evaluate_candidate"
    assert result["version"] == 1
    assert "submit_evaluation" in result["body"]
    assert "submit_evaluation" in result["tools"]


def test_load_skill_unknown_returns_error_with_available_list() -> None:
    result = invoke_tool(load_skill, name="not_a_real_skill")
    assert "error" in result
    assert "not_a_real_skill" in result["error"]
    assert "evaluate_candidate" in result["available"]
    assert len(result["available"]) == 5


def test_load_skill_deterministic_for_same_input() -> None:
    a = invoke_tool(load_skill, name="decide_candidate")
    b = invoke_tool(load_skill, name="decide_candidate")
    assert a == b


def test_load_skill_returns_tools_list_for_each_skill() -> None:
    for name in (
        "evaluate_candidate",
        "decide_candidate",
        "outreach_candidate",
        "rank_position",
        "recall_client_context",
    ):
        result = invoke_tool(load_skill, name=name)
        assert isinstance(result["tools"], list)
        assert all(isinstance(t, str) for t in result["tools"])
