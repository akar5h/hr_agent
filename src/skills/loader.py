"""Skills loader: scans src/skills/*.md, parses frontmatter, builds a frozen registry.

Skills are reasoning-memory recipes — pre-written tool-call DAGs that the LLM
loads on demand via the ``load_skill`` tool. The registry is built once at
first access and held for the process lifetime; restart to pick up edits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent
_FRONTMATTER_DELIM = "---"


class SkillLoadError(Exception):
    """Raised when a single skill file is malformed and cannot be registered."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    version: int
    triggers: tuple[str, ...]
    tools: tuple[str, ...]
    body: str
    source_path: Path


_REGISTRY: dict[str, Skill] | None = None


def _split_frontmatter(text: str, source: Path) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md into (frontmatter dict, markdown body)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        raise SkillLoadError(f"{source.name}: missing leading '---' frontmatter delimiter")

    closing_idx: int | None = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == _FRONTMATTER_DELIM:
            closing_idx = idx
            break
    if closing_idx is None:
        raise SkillLoadError(f"{source.name}: unterminated frontmatter (no closing '---')")

    frontmatter_text = "\n".join(lines[1:closing_idx])
    body = "\n".join(lines[closing_idx + 1 :]).lstrip("\n")

    try:
        parsed = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"{source.name}: invalid YAML frontmatter: {exc}") from exc

    if not isinstance(parsed, dict):
        raise SkillLoadError(f"{source.name}: frontmatter must be a YAML mapping")

    return parsed, body


def _coerce_string_tuple(value: Any, *, field: str, source: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SkillLoadError(f"{source.name}: '{field}' must be a list of strings")
    return tuple(str(item) for item in value)


def _parse_skill(source: Path) -> Skill:
    text = source.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text, source)

    for required in ("name", "description", "version"):
        if required not in frontmatter:
            raise SkillLoadError(f"{source.name}: missing required field '{required}'")

    name = str(frontmatter["name"]).strip()
    if not name:
        raise SkillLoadError(f"{source.name}: 'name' must be non-empty")

    try:
        version = int(frontmatter["version"])
    except (TypeError, ValueError) as exc:
        raise SkillLoadError(f"{source.name}: 'version' must be an integer") from exc

    return Skill(
        name=name,
        description=str(frontmatter["description"]).strip(),
        version=version,
        triggers=_coerce_string_tuple(frontmatter.get("triggers"), field="triggers", source=source),
        tools=_coerce_string_tuple(frontmatter.get("tools"), field="tools", source=source),
        body=body.rstrip() + "\n",
        source_path=source,
    )


def _load_all(skills_dir: Path = _SKILLS_DIR) -> dict[str, Skill]:
    registry: dict[str, Skill] = {}
    for path in sorted(skills_dir.glob("*.md")):
        try:
            skill = _parse_skill(path)
        except SkillLoadError as exc:
            logger.warning("skill_load_failed", extra={"file": path.name, "error": str(exc)})
            continue
        if skill.name in registry:
            logger.warning(
                "skill_duplicate_name",
                extra={
                    "skill_name": skill.name,
                    "kept": str(registry[skill.name].source_path),
                    "skipped": str(skill.source_path),
                },
            )
            continue
        registry[skill.name] = skill
    return registry


def get_skill_registry() -> dict[str, Skill]:
    """Return the process-wide skill registry, loading it on first call."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_all()
    return _REGISTRY


def reset_registry_for_tests() -> None:
    """Drop the module cache. Test helper only."""
    global _REGISTRY
    _REGISTRY = None


def build_skill_index_block() -> str:
    """Build the cached system-prompt block listing available skills."""
    registry = get_skill_registry()
    if not registry:
        return ""
    lines = [
        "## Available skills",
        "",
        "Before running a multi-step workflow, call `load_skill(name=<skill>)` and "
        "follow its procedure verbatim. Do not improvise tool ordering when a skill applies.",
        "",
    ]
    for name in sorted(registry):
        skill = registry[name]
        lines.append(f"- `{name}` (v{skill.version}) — {skill.description}")
    return "\n".join(lines) + "\n"
