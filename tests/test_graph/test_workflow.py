"""Phase 3 workflow tests."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from src.prompts.evaluation import build_system_prompt
from src.tools import ALL_TOOLS


class _StubAgent:
    def invoke(self, *args, **kwargs):
        return {"messages": [SimpleNamespace(content="ok")]}

    def stream(self, *args, **kwargs):
        yield {"messages": [SimpleNamespace(content="ok")]}


def _import_workflow_or_skip():
    try:
        return importlib.import_module("src.graph.workflow")
    except ImportError as exc:
        pytest.skip(f"Graph runtime unavailable in this environment: {exc}")


def test_build_agent_returns_compiled_graph_like_object(monkeypatch) -> None:
    workflow = _import_workflow_or_skip()
    called = {}

    monkeypatch.setattr(workflow, "build_chat_model", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(workflow, "get_checkpointer", lambda: "postgres-checkpointer")

    def _fake_create_agent(**kwargs):
        called.update(kwargs)
        return _StubAgent()

    monkeypatch.setattr(workflow, "create_agent", _fake_create_agent)

    agent = workflow.build_agent(client_id="client-techcorp", session_id="sess-1")
    assert hasattr(agent, "invoke")
    assert hasattr(agent, "stream")
    assert "system_prompt" in called
    assert "prompt" not in called
    assert called["checkpointer"] == "postgres-checkpointer"


def test_build_agent_uses_all_tools() -> None:
    workflow = _import_workflow_or_skip()
    assert len(ALL_TOOLS) == 15
    assert len(workflow.AGENT_TOOLS) == 16


def test_system_prompt_includes_client_id_and_session_id() -> None:
    prompt = build_system_prompt(client_id="acme-corp", session_id="s1")
    assert "acme-corp" in prompt
    assert "s1" in prompt


def test_trigger_ats_ranking_is_callable_tool() -> None:
    workflow = _import_workflow_or_skip()
    assert hasattr(workflow.trigger_ats_ranking, "invoke")
