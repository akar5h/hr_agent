"""Tests that the skills index is wired into the cached stable prompt."""

from __future__ import annotations

from src.prompts.evaluation import STABLE_INSTRUCTIONS, build_system_prompt_blocks
from src.skills.loader import get_skill_registry


def test_stable_prompt_contains_skill_index_header() -> None:
    assert "## Available skills" in STABLE_INSTRUCTIONS
    assert "SKILLS FIRST" in STABLE_INSTRUCTIONS


def test_stable_prompt_lists_every_registered_skill() -> None:
    registry = get_skill_registry()
    for name in registry:
        assert f"`{name}`" in STABLE_INSTRUCTIONS, f"missing index line for {name}"


def test_stable_prompt_does_not_inline_skill_bodies() -> None:
    """The whole point of skills is that bodies load on demand."""
    registry = get_skill_registry()
    for skill in registry.values():
        # A body is something like "## When to use\n..." — pick a body-only
        # marker that should never appear in the stable prompt itself.
        assert "## When to use" not in STABLE_INSTRUCTIONS or skill.body.count("## When to use") <= 1


def test_skills_index_lives_in_cached_block() -> None:
    blocks = build_system_prompt_blocks(
        client_id="c", session_id="s", model_name="anthropic/claude-3.5-haiku"
    )
    cached_text = blocks[0]["text"]
    dynamic_text = blocks[1]["text"]
    assert "Available skills" in cached_text
    assert "evaluate_candidate" in cached_text
    # Skill names must not leak into the dynamic block — that would defeat caching.
    for name in ("evaluate_candidate", "decide_candidate", "outreach_candidate"):
        assert name not in dynamic_text


def test_stable_prompt_load_skill_instruction_present() -> None:
    assert "load_skill(name=" in STABLE_INSTRUCTIONS
