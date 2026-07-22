"""Web search tool using Tavily."""

from __future__ import annotations

import os

from pydantic import BaseModel

from src.cache import session_dedup
from src.cache.tool_cache import cached_tool
from src.guardrails.session_context import get_session_id
from src.tools._compat import tool

_DEDUP_NOTE = (
    "You already ran this exact web search earlier in this session (identical "
    "query). Reusing the cached result — do not search the web again for this; "
    "use the data you already have."
)


class SearchWebInput(BaseModel):
    """Input schema for search_web."""

    query: str
    max_results: int = 5


@tool(args_schema=SearchWebInput)
@cached_tool(ttl_seconds=3600)
def search_web(query: str, max_results: int = 5) -> list[dict]:
    """Search the web for information about a candidate or topic."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return [{"error": "TAVILY_API_KEY not set"}]

    sid = get_session_id()
    norm = " ".join(query.lower().split())
    key = f"{norm}::{max_results}"
    if sid and session_dedup.seen(sid, key):
        session_dedup.bump(sid, key)
        prior_results = session_dedup.get(sid, key) or []
        return [{"_dedup_note": _DEDUP_NOTE}] + prior_results

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=max_results)
        results = response.get("results", []) if isinstance(response, dict) else []
        results = [
            {
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "content": str(result.get("content", ""))[:500],
                "score": result.get("score", 0.0),
            }
            for result in results
        ]
        if sid:
            session_dedup.put(sid, key, results)
        return results
    except Exception as exc:
        return [{"error": str(exc)}]
