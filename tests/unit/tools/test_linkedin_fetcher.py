"""Unit tests for LinkedIn fixture fetcher."""

from __future__ import annotations

from src.tools.linkedin_fetcher import fetch_linkedin
from tests.unit.tools.utils import invoke_tool


def test_fetch_linkedin_happy_path() -> None:
    result = invoke_tool(fetch_linkedin, linkedin_url="https://linkedin.com/in/alice-chen-dev")
    assert result["name"] == "Alice Chen"
    assert "bio" in result


def test_fetch_linkedin_not_found() -> None:
    result = invoke_tool(fetch_linkedin, linkedin_url="https://linkedin.com/in/unknown-user")
    assert result == {"error": "Profile not found", "url": "https://linkedin.com/in/unknown-user"}


def test_fetch_linkedin_accepts_trailing_slash() -> None:
    result = invoke_tool(fetch_linkedin, linkedin_url="https://linkedin.com/in/charlie-kim-ml/")
    assert "SYSTEM:" in result["bio"]

