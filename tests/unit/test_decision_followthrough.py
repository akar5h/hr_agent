"""Guard tests for the eval->decide terminal-framing fix (candidate release v1).

The baseline agent stopped after submit_evaluation in 69% of scored-candidate traces
because the tool docstring + system prompt + evaluate_candidate skill all framed
submit_evaluation as the terminal action. These tests pin the corrected framing:
scoring is not the task's terminal step when the user's goal is a decision, and the
old "always call submit_evaluation as the last tool" instruction is removed. They also
pin the guard against over-action (evaluate-only requests must still stop at eval).
"""

from __future__ import annotations

from pathlib import Path

from src.prompts.evaluation import PROMPT_VERSION, build_system_prompt
from src.tools.database_tools import submit_evaluation

SKILL_PATH = Path(__file__).resolve().parents[2] / "src" / "skills" / "evaluate_candidate.md"


def test_prompt_version_bumped_for_release() -> None:
    assert PROMPT_VERSION == "candidate-screening-v3-decision-followthrough"


def test_system_prompt_drops_terminal_submit_evaluation_framing() -> None:
    prompt = build_system_prompt(client_id="c", session_id="s")
    # The two lines that taught the model scoring == end-of-task must be gone.
    assert "Always call submit_evaluation as the last tool" not in prompt
    assert "final response MUST come AFTER calling submit_evaluation" not in prompt


def test_system_prompt_has_conditional_decision_followthrough() -> None:
    prompt = build_system_prompt(client_id="c", session_id="s")
    # Decision must follow eval when the goal is a decision...
    assert "shortlist_candidate or reject_candidate" in prompt
    assert "a submitted score alone does NOT commit a decision" in prompt
    # ...but evaluate-only requests must still stop (over-action guard).
    assert "do not commit an unrequested decision" in prompt
    assert "The choice is dictated by the user's request, not by the score" in prompt


def test_submit_evaluation_docstring_reframed() -> None:
    desc = submit_evaluation.description  # @tool description derives from the docstring
    assert "last step of SCORING, not necessarily of the task" in desc
    assert "follow this call with shortlist_candidate or reject_candidate" in desc
    assert "MUST be called as the last step of every candidate evaluation" not in desc


def test_evaluate_candidate_skill_has_decision_followthrough() -> None:
    body = SKILL_PATH.read_text(encoding="utf-8")
    assert "Decision follow-through" in body
    assert "load `decide_candidate`" in body
    assert "do not commit an unrequested decision" in body
