"""TTL cache for tool outputs with optional Redis backend."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import time
from typing import Any, Optional


class _InMemoryBackend:
    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at < time.time():
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._store[key] = (value, time.time() + ttl_seconds)


class _RedisBackend:
    def __init__(self, redis_url: str) -> None:
        import redis

        self._client = redis.from_url(redis_url, decode_responses=True)

    def get(self, key: str) -> Optional[Any]:
        raw = self._client.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._client.setex(key, ttl_seconds, json.dumps(value, default=str))


class ToolCache:
    def __init__(self) -> None:
        redis_url = os.getenv("REDIS_URL", "").strip()
        if not redis_url:
            self._backend: Any = _InMemoryBackend()
            return
        try:
            self._backend = _RedisBackend(redis_url)
        except Exception:
            self._backend = _InMemoryBackend()

    def _key(self, tool_name: str, payload: Any) -> str:
        serialized = json.dumps(payload, sort_keys=True, default=str)
        digest = hashlib.sha256(f"{tool_name}:{serialized}".encode("utf-8")).hexdigest()
        return f"tool_cache:{tool_name}:{digest}"

    def get(self, tool_name: str, payload: Any) -> Optional[Any]:
        return self._backend.get(self._key(tool_name, payload))

    def set(self, tool_name: str, payload: Any, value: Any, ttl_seconds: int) -> None:
        self._backend.set(self._key(tool_name, payload), value, ttl_seconds)


_CACHE = ToolCache()


def cached_tool(ttl_seconds: int):
    """Decorator to memoize deterministic tool function outputs."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            payload = {"args": args, "kwargs": kwargs}
            cached = _CACHE.get(func.__name__, payload)
            if cached is not None:
                return cached
            result = func(*args, **kwargs)
            _CACHE.set(func.__name__, payload, result, ttl_seconds)
            return result

        return wrapper

    return decorator
