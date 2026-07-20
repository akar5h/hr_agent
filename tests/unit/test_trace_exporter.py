"""Tests for the enriched trace exporter (schema normalized-trace-v2).

Covers the observability-only capture: bounded result summaries (preserving resume
safety fields), the per-session sub-agent span buffer, and the _wrap_tool structured
capture (name/arguments/result_summary/status/error/latency_ms/cached). Agent behavior
is unchanged — these assert only what the exporter records.
"""

from __future__ import annotations

import pytest


# --- pure helpers: no heavy imports -------------------------------------------------

def test_trace_capture_buffer_roundtrip() -> None:
    from src.observability import trace_capture as tc

    tc.reset_sub_agents("s1")
    assert tc.get_sub_agents("s1") == []
    tc.record_sub_agent("s1", {"type": "sql_generator", "generated_sql": "SELECT 1"})
    tc.record_sub_agent("s1", {"type": "ats_sub_agent"})
    got = tc.get_sub_agents("s1")
    assert len(got) == 2 and got[0]["type"] == "sql_generator"
    tc.reset_sub_agents("s1")
    assert tc.get_sub_agents("s1") == []
    # None session id is a no-op, never raises
    tc.record_sub_agent(None, {"x": 1})
    assert tc.get_sub_agents(None) == []


# --- _wrap_tool capture (needs the agent graph import; skips if deps unavailable) ---

def _wf():
    return pytest.importorskip("src.graph.workflow", reason="agent graph deps unavailable locally")


def test_summarize_result_passthrough_small() -> None:
    workflow = _wf()
    val = {"exists": True, "name": "Alice"}
    assert workflow._summarize_result(val) == val


def test_summarize_result_truncates_but_keeps_safety_fields() -> None:
    workflow = _wf()
    big = {
        "text": "x" * 5000,
        "truncated": True,
        "hidden_text_detected": True,
        "suspicious_instruction_detected": False,
        "warnings": ["redacted 2 lines"],
    }
    out = workflow._summarize_result(big)
    assert out["_truncated"] is True
    assert len(out["_preview"]) == workflow._RESULT_SUMMARY_CAP
    # resume safety signals survive truncation
    assert out["hidden_text_detected"] is True
    assert out["warnings"] == ["redacted 2 lines"]


def test_wrap_tool_captures_structured_call() -> None:
    workflow = _wf()
    from langchain_core.tools import tool as lctool

    @lctool
    def echo(x: str) -> dict:
        """echo tool"""
        return {"echoed": x}

    sess = "sess-wraptool-ok"
    workflow.reset_turn_tool_cache(sess)
    wrapped = workflow._wrap_tool(echo, sess, "client-x")
    wrapped.invoke({"x": "hi"})
    calls = workflow.get_turn_tool_calls(sess)
    assert len(calls) == 1
    c = calls[0]
    assert c["name"] == "echo"
    assert c["arguments"] == {"x": "hi"}
    assert c["result_summary"] == {"echoed": "hi"}
    assert c["status"] == "ok" and c["error"] is None
    assert isinstance(c["latency_ms"], int) and c["latency_ms"] >= 0
    workflow.reset_turn_tool_cache(sess)
    assert workflow.get_turn_tool_calls(sess) == []


def test_wrap_tool_captures_error_and_reraises() -> None:
    workflow = _wf()
    from langchain_core.tools import tool as lctool

    @lctool
    def boom(x: str) -> dict:
        """always errors"""
        raise ValueError("kaboom")

    sess = "sess-wraptool-err"
    workflow.reset_turn_tool_cache(sess)
    wrapped = workflow._wrap_tool(boom, sess, "client-x")
    with pytest.raises(Exception):
        wrapped.invoke({"x": "hi"})
    calls = workflow.get_turn_tool_calls(sess)
    assert len(calls) == 1
    assert calls[0]["status"] == "error"
    assert "kaboom" in calls[0]["error"]
