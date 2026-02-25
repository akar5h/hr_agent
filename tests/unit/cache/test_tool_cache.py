"""Unit tests for tool cache backends."""

from __future__ import annotations

import time

from src.cache.tool_cache import ToolCache


def test_key_stability() -> None:
    cache = ToolCache()
    key_a = cache._key("search_web", {"query": "alice", "max_results": 5})
    key_b = cache._key("search_web", {"max_results": 5, "query": "alice"})
    assert key_a == key_b


def test_ttl_expiry() -> None:
    cache = ToolCache()
    payload = {"query": "bob"}
    cache.set("search_web", payload, {"ok": True}, ttl_seconds=1)
    assert cache.get("search_web", payload) == {"ok": True}
    time.sleep(1.1)
    assert cache.get("search_web", payload) is None
