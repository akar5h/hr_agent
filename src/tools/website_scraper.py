"""Website scraping tool for candidate portfolio pages."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel

from src.cache.tool_cache import cached_tool
from src.observability.decorators import traced
from src.tools._compat import tool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEBSITE_FIXTURES_DIR = PROJECT_ROOT / "fixtures" / "websites"
WEBSITE_FIXTURE_MAP = {
    "dianapatel.io": "diana-patel.html",
    "alicechen.dev": "alice-chen.html",
}
MAX_WEBSITE_CHARS = 2000


class ScrapeWebsiteInput(BaseModel):
    """Input schema for scrape_website."""

    url: str


@traced(name="load-html")
def _load_html(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()
    fixture_name = WEBSITE_FIXTURE_MAP.get(hostname)
    if fixture_name:
        fixture_path = WEBSITE_FIXTURES_DIR / fixture_name
        return fixture_path.read_text(encoding="utf-8")

    response = requests.get(
        url,
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    return response.text


def _validate_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@tool(args_schema=ScrapeWebsiteInput)
@cached_tool(ttl_seconds=1800)
def scrape_website(url: str) -> dict:
    """Scrape a personal website and return extracted text content."""
    try:
        if not _validate_url(url):
            return {"error": f"Invalid URL: {url}", "url": url}

        html = _load_html(url)
        soup = BeautifulSoup(html, "html.parser")

        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        description = ""
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            description = str(meta.get("content")).strip()

        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
        about_text = "\n".join(text for text in paragraphs if text)
        if len(about_text) > MAX_WEBSITE_CHARS:
            about_text = about_text[:MAX_WEBSITE_CHARS] + "\n[... truncated for context efficiency ...]"

        return {
            "title": title,
            "description": description,
            "about_text": about_text,
            "url": url,
        }
    except Exception as exc:
        return {"error": str(exc), "url": url}
