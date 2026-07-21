"""Unit tests for the exact-duplicate loop nudge in `src.graph.workflow._wrap_tool`.

The per-turn deterministic cache in `_wrap_tool` already short-circuits
re-execution of identical tool calls, but does so silently — the agent never
sees a signal that it already has the answer and keeps re-issuing the same
call. For a narrow tool set (`query_database`, `search_web`), an exact repeat
within the same session gets a `_dedup_note` prepended to the (still cached,
still not re-executed) result. The dedup state lives in `session_dedup`, so it
survives a per-turn cache reset (`reset_turn_tool_cache`) — that is the whole
point: it must catch repeats across turns, not just within one.
"""

from __future__ import annotations

from src.cache import session_dedup
from src.graph.workflow import _LOOP_NUDGE_NOTE, _wrap_tool, reset_turn_tool_cache


class _FakeTool:
    """Minimal StructuredTool-like stand-in that counts invocations."""

    def __init__(self, name: str, rows: list[dict]):
        self.name = name
        self.description = f"fake {name}"
        self.args_schema = None
        self.return_direct = False
        self.rows = rows
        self.call_count = 0

    def invoke(self, kwargs: dict) -> list[dict]:
        self.call_count += 1
        return list(self.rows)


def test_first_call_executes_and_has_no_note() -> None:
    session_dedup.reset("s1")
    fake = _FakeTool("query_database", [{"id": "pos-1"}])
    wrapped = _wrap_tool(fake, session_id="s1", client_id="c1")

    result = wrapped.invoke({"query_intent": "list open positions"})

    assert fake.call_count == 1
    assert result == [{"id": "pos-1"}]


def test_identical_repeat_skips_execution_and_adds_note() -> None:
    session_dedup.reset("s2")
    fake = _FakeTool("query_database", [{"id": "pos-1"}])
    wrapped = _wrap_tool(fake, session_id="s2", client_id="c1")

    first = wrapped.invoke({"query_intent": "list open positions"})
    second = wrapped.invoke({"query_intent": "list open positions"})

    assert fake.call_count == 1, "identical repeat must not re-execute the tool"
    assert first == [{"id": "pos-1"}]
    assert second[0] == {"_dedup_note": _LOOP_NUDGE_NOTE}
    assert second[1:] == [{"id": "pos-1"}]


def test_different_args_still_execute() -> None:
    session_dedup.reset("s3")
    fake = _FakeTool("query_database", [{"id": "pos-1"}])
    wrapped = _wrap_tool(fake, session_id="s3", client_id="c1")

    wrapped.invoke({"query_intent": "list open positions"})
    result = wrapped.invoke({"query_intent": "list closed positions"})

    assert fake.call_count == 2
    assert "_dedup_note" not in (result[0] if result else {})


def test_non_target_tool_is_not_nudged() -> None:
    session_dedup.reset("s4")
    fake = _FakeTool("shortlist_candidate", [{"id": "cand-1"}])
    wrapped = _wrap_tool(fake, session_id="s4", client_id="c1")

    first = wrapped.invoke({"candidate_id": "cand-1"})
    second = wrapped.invoke({"candidate_id": "cand-1"})

    # Not in _LOOP_NUDGE_TOOLS, so no dedup note — but the pre-existing
    # per-turn deterministic cache also doesn't apply (not in
    # _DETERMINISTIC_TOOL_NAMES either), so it just re-executes plainly.
    assert fake.call_count == 2
    assert second == [{"id": "cand-1"}]


def test_repeat_survives_turn_reset() -> None:
    session_dedup.reset("s5")
    fake = _FakeTool("search_web", [{"title": "Priya"}])
    wrapped = _wrap_tool(fake, session_id="s5", client_id="c1")

    first = wrapped.invoke({"query": "find priya"})
    reset_turn_tool_cache("s5")
    second = wrapped.invoke({"query": "find priya"})

    assert fake.call_count == 1, "cross-turn identical repeat must still be caught"
    assert first == [{"title": "Priya"}]
    assert second[0] == {"_dedup_note": _LOOP_NUDGE_NOTE}
    assert second[1:] == [{"title": "Priya"}]
