"""Integration tests for Phase 4 ATS sub-agent."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from src.database import db
from src.database.seed import run_seed
from tests.unit.tools.utils import invoke_tool

RUBRIC = {"technical": 40, "experience": 30, "culture_fit": 20, "communication": 10}


def _setup_ats_data(monkeypatch, tmp_path) -> None:
    db_file = tmp_path / "ats.db"
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_file))
    run_seed()
    with db.get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO evaluations (
                id, candidate_id, position_id, client_id,
                technical_score, experience_score, culture_score, communication_score,
                overall_score, reasoning, recommendation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "eval-alice",
                "cand-alice-chen",
                "pos-techcorp-spe",
                "client-techcorp",
                8.0,
                7.0,
                8.0,
                9.0,
                7.8,
                "Strong backend systems profile",
                "HIRE",
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO evaluations (
                id, candidate_id, position_id, client_id,
                technical_score, experience_score, culture_score, communication_score,
                overall_score, reasoning, recommendation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "eval-bob",
                "cand-bob-martinez",
                "pos-techcorp-spe",
                "client-techcorp",
                6.0,
                6.0,
                7.0,
                6.0,
                6.2,
                "Solid basics, less depth",
                "CONSIDER",
            ),
        )


def _import_ats_subgraph_or_skip():
    try:
        return importlib.import_module("src.graph.ats_subgraph")
    except ImportError as exc:
        pytest.skip(f"ATS runtime unavailable in this environment: {exc}")


def _import_workflow_or_skip():
    try:
        return importlib.import_module("src.graph.workflow")
    except ImportError as exc:
        pytest.skip(f"Workflow runtime unavailable in this environment: {exc}")


def test_build_ats_agent_returns_compiled_like_object(monkeypatch, tmp_path) -> None:
    ats_subgraph = _import_ats_subgraph_or_skip()
    _setup_ats_data(monkeypatch, tmp_path)
    called = {}

    monkeypatch.setattr(ats_subgraph, "ChatAnthropic", lambda **kwargs: SimpleNamespace(**kwargs))

    def _fake_create_agent(**kwargs):
        called.update(kwargs)
        return SimpleNamespace(invoke=lambda *_a, **_k: {"messages": []}, stream=lambda *_a, **_k: iter(()))

    monkeypatch.setattr(ats_subgraph, "create_agent", _fake_create_agent)

    agent = ats_subgraph.build_ats_agent("client-techcorp", "pos-techcorp-spe", RUBRIC)
    assert hasattr(agent, "invoke")
    assert hasattr(agent, "stream")
    assert "system_prompt" in called
    assert "prompt" not in called
    assert called["tools"] == ats_subgraph.ATS_TOOLS


def test_fetch_candidates_for_position_returns_evaluated_rows(monkeypatch, tmp_path) -> None:
    ats_subgraph = _import_ats_subgraph_or_skip()
    _setup_ats_data(monkeypatch, tmp_path)
    result = invoke_tool(
        ats_subgraph.fetch_candidates_for_position,
        position_id="pos-techcorp-spe",
        client_id="client-techcorp",
    )
    assert len(result) == 2
    assert {row["candidate_id"] for row in result} == {"cand-alice-chen", "cand-bob-martinez"}


def test_score_candidate_applies_weighted_formula(monkeypatch, tmp_path) -> None:
    ats_subgraph = _import_ats_subgraph_or_skip()
    _setup_ats_data(monkeypatch, tmp_path)
    result = invoke_tool(
        ats_subgraph.score_candidate,
        candidate_id="cand-alice-chen",
        position_id="pos-techcorp-spe",
        client_id="client-techcorp",
        rubric=RUBRIC,
    )
    assert result["name"] == "Alice Chen"
    assert result["weighted_score"] == 7.8
    assert result["breakdown"]["technical"]["weighted"] == 3.2


def test_rank_candidates_orders_descending(monkeypatch, tmp_path) -> None:
    ats_subgraph = _import_ats_subgraph_or_skip()
    _setup_ats_data(monkeypatch, tmp_path)
    ranked = invoke_tool(
        ats_subgraph.rank_candidates,
        scores=[
            {"candidate_id": "cand-b", "name": "B", "weighted_score": 6.2},
            {"candidate_id": "cand-a", "name": "A", "weighted_score": 7.8},
        ],
    )
    assert ranked[0]["candidate_id"] == "cand-a"
    assert ranked[0]["rank"] == 1
    assert ranked[1]["rank"] == 2


def test_generate_ats_report_contains_recommendation(monkeypatch, tmp_path) -> None:
    ats_subgraph = _import_ats_subgraph_or_skip()
    _setup_ats_data(monkeypatch, tmp_path)
    report = invoke_tool(
        ats_subgraph.generate_ats_report,
        position_id="pos-techcorp-spe",
        client_id="client-techcorp",
        ranked_candidates=[
            {"rank": 1, "candidate_id": "cand-a", "name": "Alice Chen", "weighted_score": 7.8},
            {"rank": 2, "candidate_id": "cand-b", "name": "Bob Martinez", "weighted_score": 6.2},
        ],
    )
    assert "# ATS Ranking Report" in report
    assert "Top candidate: **Alice Chen**" in report


def test_trigger_ats_ranking_returns_report(monkeypatch, tmp_path) -> None:
    workflow = _import_workflow_or_skip()
    _setup_ats_data(monkeypatch, tmp_path)

    class _FakeATSAgent:
        def invoke(self, *_args, **_kwargs):
            return {"messages": [SimpleNamespace(content="# ATS Ranking Report\nTop candidate: Alice Chen")]}

    monkeypatch.setattr(workflow, "build_ats_agent", lambda **kwargs: _FakeATSAgent())

    result = invoke_tool(
        workflow.trigger_ats_ranking,
        position_id="pos-techcorp-spe",
        client_id="client-techcorp",
    )
    assert "ATS Ranking Report" in result
    assert "Alice Chen" in result
