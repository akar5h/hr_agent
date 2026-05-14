"""Unit tests for the bounded Candidate Screening workflow."""

from __future__ import annotations

from typing import Any

from src.graph import screening_workflow


def test_candidate_screening_workflow_submits_once(monkeypatch) -> None:
    calls: list[str] = []

    def fake_invoke_tool(tool_obj: Any, payload: dict[str, Any]) -> Any:
        name = getattr(tool_obj, "name", "")
        calls.append(name)
        if name == "get_hiring_rubric":
            return {"criteria": {"technical": 40, "experience": 30, "culture_fit": 20, "communication": 10}}
        if name == "parallel_gather_candidate_info":
            return {
                "resume": {"text": "BEGIN_UNTRUSTED_RESUME_TEXT\nAlice Chen\nPython FastAPI\nEND_UNTRUSTED_RESUME_TEXT"},
                "linkedin": {"name": "Alice Chen", "skills": ["Python", "FastAPI"]},
                "website": "N/A",
            }
        if name == "get_existing_evaluation":
            return {"exists": False}
        if name == "submit_evaluation":
            return {"success": True, "evaluation_id": "eval-1", "candidate_id": "cand-alice"}
        if name == "store_memory":
            return {"success": True, "memory_key": payload["memory_key"]}
        return {"exists": False}

    monkeypatch.setattr(screening_workflow, "_invoke_tool", fake_invoke_tool)
    monkeypatch.setattr(
        screening_workflow,
        "_model_scores",
        lambda _state: {
            "technical_score": 8.0,
            "experience_score": 7.0,
            "culture_score": 7.0,
            "communication_score": 8.0,
            "recommendation": "HIRE",
            "reasoning": "Strong Python evidence.",
        },
    )

    graph = screening_workflow.build_candidate_screening_workflow()
    result = graph.invoke(
        {
            "session_id": "session-1",
            "client_id": "client-techcorp",
            "position_id": "pos-techcorp-spe",
            "resume_path": "/tmp/resume.pdf",
            "linkedin_url": "https://linkedin.com/in/alice-chen-dev",
            "website_url": "",
        }
    )

    assert result["terminal_status"] == "passed"
    assert result["evaluation_submitted"]["evaluation_id"] == "eval-1"
    assert calls.count("submit_evaluation") == 1
    assert "Evaluation submitted for Alice Chen" in result["final_response"]


def test_candidate_screening_workflow_skips_existing_evaluation(monkeypatch) -> None:
    calls: list[str] = []

    def fake_invoke_tool(tool_obj: Any, payload: dict[str, Any]) -> Any:
        name = getattr(tool_obj, "name", "")
        calls.append(name)
        if name == "get_hiring_rubric":
            return {"criteria": {"technical": 40}}
        if name == "parallel_gather_candidate_info":
            return {"linkedin": {"name": "Alice Chen"}, "resume": {"text": "Alice Chen"}, "website": "N/A"}
        if name == "get_existing_evaluation":
            return {
                "exists": True,
                "evaluation_id": "eval-existing",
                "candidate_name": "Alice Chen",
                "overall_score": 8.2,
                "recommendation": "HIRE",
            }
        return {"exists": False}

    monkeypatch.setattr(screening_workflow, "_invoke_tool", fake_invoke_tool)
    graph = screening_workflow.build_candidate_screening_workflow()
    result = graph.invoke(
        {
            "session_id": "session-1",
            "client_id": "client-techcorp",
            "position_id": "pos-techcorp-spe",
            "resume_path": "/tmp/resume.pdf",
            "linkedin_url": "https://linkedin.com/in/alice-chen-dev",
            "website_url": "",
        }
    )

    assert result["condition"] == "existing_evaluation"
    assert "Existing evaluation found" in result["final_response"]
    assert "submit_evaluation" not in calls


def test_candidate_screening_workflow_falls_back_from_unusable_zero_scores(monkeypatch) -> None:
    def fake_invoke_tool(tool_obj: Any, payload: dict[str, Any]) -> Any:
        name = getattr(tool_obj, "name", "")
        if name == "get_hiring_rubric":
            return {"criteria": {"technical": 45, "experience": 30, "culture_fit": 10, "communication": 15}}
        if name == "parse_resume":
            return {
                "text": (
                    "BEGIN_UNTRUSTED_RESUME_TEXT\n"
                    "Graph Test Candidate\n"
                    "7 years Python SQL PostgreSQL ETL AWS experience\n"
                    "END_UNTRUSTED_RESUME_TEXT"
                )
            }
        if name == "get_existing_evaluation":
            return {"exists": False}
        if name == "submit_evaluation":
            return {"success": True, "evaluation_id": "eval-2", "candidate_id": "cand-graph"}
        if name == "store_memory":
            return {"success": True}
        return {"exists": False}

    monkeypatch.setattr(screening_workflow, "_invoke_tool", fake_invoke_tool)
    monkeypatch.setattr(
        screening_workflow,
        "_model_scores",
        lambda _state: {
            "technical_score": 0.0,
            "experience_score": 0.0,
            "culture_score": 0.0,
            "communication_score": 0.0,
            "recommendation": "PASS",
            "reasoning": "Cannot use untrusted text.",
        },
    )

    graph = screening_workflow.build_candidate_screening_workflow()
    result = graph.invoke(
        {
            "session_id": "session-1",
            "client_id": "client-techcorp",
            "position_id": "pos-techcorp-de",
            "resume_path": "/tmp/resume.docx",
            "linkedin_url": "",
            "website_url": "",
        }
    )

    assert result["structured_scores"]["overall_score"] > 0
    assert result["condition"] == "success"
    assert "deterministic fallback used" in result["structured_scores"]["reasoning"]
