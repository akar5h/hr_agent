"""The load_skill tool: returns the body of a named skill for the agent to follow."""

from __future__ import annotations

from pydantic import BaseModel

from src.observability.decorators import traced
from src.skills.loader import Skill, get_skill_registry
from src.tools._compat import tool


class LoadSkillInput(BaseModel):
    """Input schema for load_skill."""

    name: str


def _skill_payload(skill: Skill) -> dict:
    return {
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "tools": list(skill.tools),
        "body": skill.body,
    }


@tool(args_schema=LoadSkillInput)
@traced(name="load-skill")
def load_skill(name: str) -> dict:
    """Load the full procedure for the named skill.

    Call this BEFORE executing a multi-step workflow such as evaluating,
    deciding on, or contacting a candidate. Then follow the returned
    `body` verbatim — do not improvise tool ordering when a skill applies.
    """
    registry = get_skill_registry()
    skill = registry.get(name)
    if skill is None:
        return {
            "error": f"unknown skill: {name!r}",
            "available": sorted(registry),
        }
    return _skill_payload(skill)
