"""Unit tests for Tavily web search tool."""

from __future__ import annotations

import sys
import types

from src.tools.web_search import search_web
from tests.unit.tools.utils import invoke_tool


def test_search_web_returns_error_when_api_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    result = invoke_tool(search_web, query="alice chen", max_results=3)
    assert result == [{"error": "TAVILY_API_KEY not set"}]


def test_search_web_happy_path(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

    class FakeTavilyClient:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def search(self, query: str, max_results: int):
            assert query == "alice chen"
            assert max_results == 2
            return {
                "results": [
                    {
                        "title": "Alice Result",
                        "url": "https://example.com/alice",
                        "content": "Snippet",
                        "score": 0.9,
                    }
                ]
            }

    monkeypatch.setitem(sys.modules, "tavily", types.SimpleNamespace(TavilyClient=FakeTavilyClient))
    result = invoke_tool(search_web, query="alice chen", max_results=2)
    assert result[0]["title"] == "Alice Result"
    assert result[0]["score"] == 0.9


def test_search_web_handles_client_error(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

    class BrokenClient:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def search(self, query: str, max_results: int):
            raise RuntimeError("tavily error")

    monkeypatch.setitem(sys.modules, "tavily", types.SimpleNamespace(TavilyClient=BrokenClient))
    result = invoke_tool(search_web, query="test", max_results=1)
    assert result == [{"error": "tavily error"}]

