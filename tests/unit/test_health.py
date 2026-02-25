"""Unit tests for health-check orchestration."""

from __future__ import annotations

from src import health


def test_run_health_check_ok(monkeypatch) -> None:
    monkeypatch.setattr(health, "check_database", lambda: {"status": "ok", "latency_ms": 1})
    monkeypatch.setattr(health, "check_model", lambda: {"status": "ok", "latency_ms": 1})
    result = health.run_health_check()
    assert result["overall"] == "ok"


def test_run_health_check_degraded(monkeypatch) -> None:
    monkeypatch.setattr(health, "check_database", lambda: {"status": "error", "latency_ms": 1})
    monkeypatch.setattr(health, "check_model", lambda: {"status": "ok", "latency_ms": 1})
    result = health.run_health_check()
    assert result["overall"] == "degraded"
