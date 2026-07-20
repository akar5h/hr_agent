"""Tests for the standard-observability trace fix (candidate release v1, Feature 2).

The baseline trace pipeline captured only tool-call args, never tool RESULTS
(observations), which is why the external analyzer could not see grounding. These pin
the two pure helpers that now capture tool observations: tool_call ids are preserved
(so results can be joined to calls), and ToolMessage results are scoped to the current
turn and length-capped with an explicit truncation flag.
"""

from __future__ import annotations

import sys
from pathlib import Path

_EXPERIMENT_DIR = Path(__file__).resolve().parents[2] / "experiments" / "historical_traffic_v0"
if str(_EXPERIMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_EXPERIMENT_DIR))

from runner.hr_bridge import (  # noqa: E402
    _MAX_TOOL_RESULT_CHARS,
    _extract_tool_calls,
    _extract_tool_results,
)


class _AIMsg:
    type = "ai"

    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


class _ToolMsg:
    type = "tool"

    def __init__(self, tool_call_id, name, content):
        self.tool_call_id = tool_call_id
        self.name = name
        self.content = content


def test_extract_tool_calls_preserves_id() -> None:
    msg = _AIMsg([{"name": "get_hiring_rubric", "args": {"position_id": "p1"}, "id": "call_1"}])
    calls = _extract_tool_calls(msg)
    assert calls == [{"name": "get_hiring_rubric", "args": {"position_id": "p1"}, "id": "call_1"}]


def test_extract_tool_results_scopes_to_current_turn() -> None:
    messages = [
        _AIMsg([{"name": "get_hiring_rubric", "args": {}, "id": "call_1"}]),
        _ToolMsg("call_0", "old_tool", "prior turn result"),   # earlier turn — excluded
        _ToolMsg("call_1", "get_hiring_rubric", '{"weights": {}}'),  # this turn — included
    ]
    results = _extract_tool_results(messages, id_filter={"call_1"})
    assert len(results) == 1
    assert results[0]["tool_call_id"] == "call_1"
    assert results[0]["name"] == "get_hiring_rubric"
    assert results[0]["result"] == '{"weights": {}}'
    assert results[0]["truncated"] is False


def test_extract_tool_results_truncates_and_flags() -> None:
    big = "x" * (_MAX_TOOL_RESULT_CHARS + 500)
    messages = [_ToolMsg("call_1", "parse_resume", big)]
    results = _extract_tool_results(messages, id_filter={"call_1"})
    assert len(results[0]["result"]) == _MAX_TOOL_RESULT_CHARS
    assert results[0]["truncated"] is True


def test_extract_tool_results_empty_filter_falls_back_to_all() -> None:
    messages = [_ToolMsg("call_9", "scrape_website", "site text")]
    results = _extract_tool_results(messages, id_filter=set())
    assert len(results) == 1
    assert results[0]["name"] == "scrape_website"
