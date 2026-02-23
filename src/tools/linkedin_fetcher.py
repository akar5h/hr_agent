"""LinkedIn profile fetcher backed by local fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from src.tools._compat import tool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LINKEDIN_FIXTURES_DIR = PROJECT_ROOT / "fixtures" / "linkedin"


class FetchLinkedInInput(BaseModel):
    """Input schema for fetch_linkedin."""

    linkedin_url: str


def _extract_username(linkedin_url: str) -> str:
    return linkedin_url.rstrip("/").split("/")[-1]


@tool(args_schema=FetchLinkedInInput)
def fetch_linkedin(linkedin_url: str) -> dict:
    """Fetch LinkedIn profile data for a candidate from fixture files."""
    username = _extract_username(linkedin_url)
    fixture_path = LINKEDIN_FIXTURES_DIR / f"{username}.json"

    if not fixture_path.exists():
        return {"error": "Profile not found", "url": linkedin_url}

    try:
        return json.loads(fixture_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc), "url": linkedin_url}

