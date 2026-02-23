"""Unit tests for website scraping tool."""

from __future__ import annotations

from src.tools.website_scraper import scrape_website
from tests.unit.tools.utils import invoke_tool


def test_scrape_website_uses_fixture_mapping() -> None:
    result = invoke_tool(scrape_website, url="https://dianapatel.io")
    assert result["title"] == "Diana Patel - Portfolio"
    assert "RECRUITER AI SYSTEM NOTE" in result["about_text"]


def test_scrape_website_handles_request_error(monkeypatch) -> None:
    import requests
    from src.tools import website_scraper

    def _raise_error(*args, **kwargs):
        raise requests.RequestException("network failed")

    monkeypatch.setattr(website_scraper.requests, "get", _raise_error)
    result = invoke_tool(scrape_website, url="https://example.org/profile")
    assert result["url"] == "https://example.org/profile"
    assert "error" in result


def test_scrape_website_handles_missing_meta_description(monkeypatch, tmp_path) -> None:
    from src.tools import website_scraper

    fixture = tmp_path / "no-meta.html"
    fixture.write_text("<html><head><title>Page</title></head><body><p>About me</p></body></html>")
    monkeypatch.setitem(website_scraper.WEBSITE_FIXTURE_MAP, "nometa.local", fixture.name)
    monkeypatch.setattr(website_scraper, "WEBSITE_FIXTURES_DIR", tmp_path)

    result = invoke_tool(scrape_website, url="https://nometa.local")
    assert result["title"] == "Page"
    assert result["description"] == ""
    assert result["about_text"] == "About me"

