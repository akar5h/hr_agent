"""Tests for the skill loader: frontmatter parsing, duplicate handling, errors."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.skills.loader import (
    Skill,
    SkillLoadError,
    _load_all,
    _parse_skill,
    _split_frontmatter,
    get_skill_registry,
)


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_parse_valid_skill(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "demo.md",
        """---
name: demo
description: A demo skill
version: 2
triggers:
  - "do the thing"
tools:
  - parse_resume
---

## When to use
Use it when X.

## Procedure
1. Step A.
""",
    )
    skill = _parse_skill(src)
    assert isinstance(skill, Skill)
    assert skill.name == "demo"
    assert skill.version == 2
    assert skill.tools == ("parse_resume",)
    assert skill.triggers == ("do the thing",)
    assert "## When to use" in skill.body
    assert skill.source_path == src


def test_missing_frontmatter_raises(tmp_path: Path) -> None:
    src = _write(tmp_path / "bad.md", "no frontmatter at all\n")
    with pytest.raises(SkillLoadError, match="missing leading"):
        _parse_skill(src)


def test_unterminated_frontmatter_raises(tmp_path: Path) -> None:
    src = _write(tmp_path / "bad.md", "---\nname: x\n")
    with pytest.raises(SkillLoadError, match="unterminated"):
        _parse_skill(src)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    # Unbalanced quote — yaml.safe_load reliably raises on this.
    src = _write(tmp_path / "bad.md", '---\nname: "unterminated\n---\nbody\n')
    with pytest.raises(SkillLoadError, match="invalid YAML"):
        _parse_skill(src)


def test_missing_required_fields_raises(tmp_path: Path) -> None:
    src = _write(tmp_path / "bad.md", "---\nname: x\n---\nbody\n")
    with pytest.raises(SkillLoadError, match="missing required field 'description'"):
        _parse_skill(src)


def test_non_int_version_raises(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "bad.md",
        "---\nname: x\ndescription: y\nversion: not-a-number\n---\n",
    )
    with pytest.raises(SkillLoadError, match="'version' must be an integer"):
        _parse_skill(src)


def test_triggers_must_be_list(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "bad.md",
        "---\nname: x\ndescription: y\nversion: 1\ntriggers: not-a-list\n---\n",
    )
    with pytest.raises(SkillLoadError, match="'triggers' must be a list"):
        _parse_skill(src)


def test_skill_is_frozen() -> None:
    skill = Skill(
        name="x",
        description="y",
        version=1,
        triggers=(),
        tools=(),
        body="",
        source_path=Path("/tmp/x.md"),
    )
    with pytest.raises(Exception):
        skill.name = "z"  # type: ignore[misc]


def test_load_all_ignores_malformed_files(tmp_path: Path) -> None:
    _write(tmp_path / "good.md", "---\nname: good\ndescription: ok\nversion: 1\n---\nbody\n")
    _write(tmp_path / "broken.md", "totally invalid")
    registry = _load_all(tmp_path)
    assert set(registry) == {"good"}


def test_load_all_drops_duplicate_names(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\nname: dup\ndescription: first\nversion: 1\n---\nA\n")
    _write(tmp_path / "b.md", "---\nname: dup\ndescription: second\nversion: 1\n---\nB\n")
    registry = _load_all(tmp_path)
    # Globbed in sorted order, so a.md wins.
    assert registry["dup"].description == "first"
    assert registry["dup"].source_path.name == "a.md"


def test_load_all_only_reads_md_files(tmp_path: Path) -> None:
    _write(tmp_path / "good.md", "---\nname: good\ndescription: ok\nversion: 1\n---\nbody\n")
    _write(tmp_path / "loader.py", "this is not a skill file")
    _write(tmp_path / "notes.txt", "neither is this")
    registry = _load_all(tmp_path)
    assert set(registry) == {"good"}


def test_split_frontmatter_strips_body(tmp_path: Path) -> None:
    text = "---\nname: x\ndescription: y\nversion: 1\n---\n\n\nbody starts here\n"
    frontmatter, body = _split_frontmatter(text, tmp_path / "x.md")
    assert frontmatter == {"name": "x", "description": "y", "version": 1}
    assert body.startswith("body starts here")


def test_real_registry_has_five_skills() -> None:
    registry = get_skill_registry()
    assert set(registry) == {
        "decide_candidate",
        "evaluate_candidate",
        "outreach_candidate",
        "rank_position",
        "recall_client_context",
    }
