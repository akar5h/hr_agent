"""Web search tool using Tavily."""

from __future__ import annotations

import os

from pydantic import BaseModel

from src.tools._compat import tool


class SearchWebInput(BaseModel):
    """Input schema for search_web."""

    query: str
    max_results: int = 5


@tool(args_schema=SearchWebInput)
def search_web(query: str, max_results: int = 5) -> list[dict]:
    """Search the web for information about a candidate or topic."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return [{"error": "TAVILY_API_KEY not set"}]

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=max_results)
        results = response.get("results", []) if isinstance(response, dict) else []
        return [
            {
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "content": result.get("content", ""),
                "score": result.get("score", 0.0),
            }
            for result in results
        ]
    except Exception as exc:
        return [{"error": str(exc)}]

