"""Skills: reasoning-memory recipes loaded on demand via the load_skill tool.

The package intentionally does NOT pre-import :mod:`src.skills.tool` to keep
prompt-construction (which only needs the loader) free of a langchain
import dependency and to avoid a circular import with :mod:`src.tools`.
Import the tool directly: ``from src.skills.tool import load_skill``.
"""

from src.skills.loader import (
    Skill,
    SkillLoadError,
    build_skill_index_block,
    get_skill_registry,
    reset_registry_for_tests,
)

__all__ = [
    "Skill",
    "SkillLoadError",
    "build_skill_index_block",
    "get_skill_registry",
    "reset_registry_for_tests",
]
