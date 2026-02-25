"""Pre-flight health checks for database + model connectivity."""

from __future__ import annotations

import json
import sys
import time
from typing import Any


def check_database() -> dict[str, Any]:
    start = time.monotonic()
    try:
        from src.database.db import get_db

        with get_db() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "latency_ms": int((time.monotonic() - start) * 1000),
        }


def check_model() -> dict[str, Any]:
    start = time.monotonic()
    try:
        from src.llm import build_chat_model

        model = build_chat_model()
        response = model.invoke("Reply with the single word: ok")
        content = getattr(response, "content", str(response)).strip().lower()
        status = "ok" if "ok" in content else "degraded"
        return {"status": status, "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "latency_ms": int((time.monotonic() - start) * 1000),
        }


def run_health_check() -> dict[str, Any]:
    db = check_database()
    model = check_model()
    overall = "ok" if db["status"] == "ok" and model["status"] == "ok" else "degraded"
    return {"overall": overall, "database": db, "model": model}


if __name__ == "__main__":
    result = run_health_check()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["overall"] == "ok" else 1)
