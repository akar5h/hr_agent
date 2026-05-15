"""Content-level invariants on every shipped skill file.

These tests guard against drift between the skills and the rest of the codebase:
- frontmatter name matches the filename stem
- description stays tight (selection signal degrades fast past ~200 chars)
- every tool a skill claims to orchestrate actually exists in ALL_TOOLS
"""

from __future__ import annotations

import pytest

from src.skills.loader import Skill, get_skill_registry
from src.tools import ALL_TOOLS

_REGISTERED_TOOL_NAMES = {
    getattr(tool_obj, "name", "") for tool_obj in ALL_TOOLS
} | {"trigger_ats_ranking"}  # registered on the chat agent only, not exported

DESCRIPTION_MAX_CHARS = 200


@pytest.fixture(scope="module")
def registry() -> dict[str, Skill]:
    return get_skill_registry()


def test_every_skill_filename_matches_name(registry: dict[str, Skill]) -> None:
    for name, skill in registry.items():
        assert skill.source_path.stem == name, (
            f"skill {name!r} lives at {skill.source_path.name}; "
            f"filename stem must match the frontmatter `name`"
        )


def test_every_skill_description_within_length_cap(registry: dict[str, Skill]) -> None:
    for name, skill in registry.items():
        assert 0 < len(skill.description) <= DESCRIPTION_MAX_CHARS, (
            f"skill {name!r} description is {len(skill.description)} chars; "
            f"keep under {DESCRIPTION_MAX_CHARS} so it stays useful for selection"
        )


def test_every_skill_version_is_positive(registry: dict[str, Skill]) -> None:
    for name, skill in registry.items():
        assert skill.version >= 1, f"skill {name!r} has non-positive version {skill.version}"


def test_every_listed_tool_exists(registry: dict[str, Skill]) -> None:
    for name, skill in registry.items():
        for tool_name in skill.tools:
            assert tool_name in _REGISTERED_TOOL_NAMES, (
                f"skill {name!r} references unknown tool {tool_name!r}; "
                f"this would deceive the model"
            )


def test_every_skill_body_has_a_procedure_section(registry: dict[str, Skill]) -> None:
    for name, skill in registry.items():
        assert "## Procedure" in skill.body, (
            f"skill {name!r} body is missing the '## Procedure' section"
        )
