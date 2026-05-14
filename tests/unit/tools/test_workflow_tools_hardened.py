"""DB-free tests for workflow tool hardening (idempotency + tenant boundary)."""

from __future__ import annotations

from typing import Any

import pytest

from src.guardrails import rate_limiter
from src.tools import workflow_tools
from tests.unit.tools.utils import invoke_tool


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Stop the module-level counters from bleeding across tests."""
    rate_limiter._per_tool_counts.clear()
    rate_limiter._session_counts.clear()
    yield
    rate_limiter._per_tool_counts.clear()
    rate_limiter._session_counts.clear()


def _patch_candidate(monkeypatch, *, client_id: str = "client-techcorp") -> None:
    def _fake_lookup(candidate_id: str, _client_id: str) -> dict[str, Any] | None:
        if _client_id != client_id:
            return None
        return {
            "id": candidate_id,
            "name": "Alice",
            "email": "alice@example.com",
            "client_id": _client_id,
        }

    monkeypatch.setattr(workflow_tools, "_lookup_candidate_in_client", _fake_lookup)


def _patch_audit(monkeypatch, sink: list[dict[str, Any]]) -> None:
    def _capture(**kwargs):
        sink.append(kwargs)

    monkeypatch.setattr(workflow_tools, "record_audit_event", _capture)


def test_shortlist_rejects_unknown_candidate_in_client(monkeypatch) -> None:
    monkeypatch.setattr(
        workflow_tools,
        "_lookup_candidate_in_client",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(workflow_tools, "record_audit_event", lambda **_kw: None)
    monkeypatch.setattr(
        workflow_tools,
        "_record_decision",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    result = invoke_tool(
        workflow_tools.shortlist_candidate,
        candidate_id="cand-other-client",
        position_id="pos-1",
        client_id="client-techcorp",
        reason="x",
        session_id="sess-1",
    )
    assert result["success"] is False
    assert "not found for this client" in result["error"]


def test_shortlist_idempotent_replay_returns_existing(monkeypatch) -> None:
    audit_sink: list[dict[str, Any]] = []
    _patch_candidate(monkeypatch)
    _patch_audit(monkeypatch, audit_sink)

    calls: list[dict[str, Any]] = []

    def fake_record_decision(**kwargs):
        calls.append(kwargs)
        was_new = len(calls) == 1
        return {"id": 42 if was_new else 42, "decision": "shortlist", "reason": "fit"}, was_new

    monkeypatch.setattr(workflow_tools, "_record_decision", fake_record_decision)

    first = invoke_tool(
        workflow_tools.shortlist_candidate,
        candidate_id="cand-1",
        position_id="pos-1",
        client_id="client-techcorp",
        reason="fit",
        session_id="sess-1",
    )
    second = invoke_tool(
        workflow_tools.shortlist_candidate,
        candidate_id="cand-1",
        position_id="pos-1",
        client_id="client-techcorp",
        reason="fit",
        session_id="sess-1",
    )

    assert first["success"] is True
    assert first["idempotent_replay"] is False
    assert second["success"] is True
    assert second["idempotent_replay"] is True
    assert second["decision_id"] == first["decision_id"]
    assert [event["outcome"] for event in audit_sink] == ["ok", "duplicate"]


def test_send_email_rate_limited(monkeypatch) -> None:
    _patch_candidate(monkeypatch)
    monkeypatch.setattr(workflow_tools, "record_audit_event", lambda **_kw: None)
    monkeypatch.setattr(
        workflow_tools,
        "_record_email",
        lambda **_kw: ({"id": 1, "subject": "x", "status": "queued"}, True),
    )
    monkeypatch.setitem(rate_limiter.DEFAULT_PER_TOOL_LIMITS, "send_candidate_email", 2)

    for _ in range(2):
        result = invoke_tool(
            workflow_tools.send_candidate_email,
            candidate_id="cand-1",
            client_id="client-techcorp",
            subject="hello",
            body="body",
            session_id="sess-rate",
        )
        assert result["success"] is True

    blocked = invoke_tool(
        workflow_tools.send_candidate_email,
        candidate_id="cand-1",
        client_id="client-techcorp",
        subject="hello-again",
        body="body",
        session_id="sess-rate",
    )
    assert blocked["success"] is False
    assert "send_candidate_email quota" in blocked["error"]


def test_reject_candidate_audits_on_db_error(monkeypatch) -> None:
    audit_sink: list[dict[str, Any]] = []
    _patch_candidate(monkeypatch)
    _patch_audit(monkeypatch, audit_sink)

    def boom(**_kw):
        raise RuntimeError("db connection lost")

    monkeypatch.setattr(workflow_tools, "_record_decision", boom)

    result = invoke_tool(
        workflow_tools.reject_candidate,
        candidate_id="cand-1",
        position_id="pos-1",
        client_id="client-techcorp",
        reason="no",
        session_id="sess-err",
    )
    assert result["success"] is False
    assert "db connection lost" in result["error"]
    assert audit_sink and audit_sink[-1]["outcome"] == "error"


def test_idempotency_key_distinguishes_actions() -> None:
    shortlist_key = workflow_tools._idempotency_key(
        "decision", "shortlist", "sess-1", "cand-1", "pos-1"
    )
    reject_key = workflow_tools._idempotency_key(
        "decision", "reject", "sess-1", "cand-1", "pos-1"
    )
    assert shortlist_key != reject_key
    # Same inputs hash deterministically.
    again = workflow_tools._idempotency_key(
        "decision", "shortlist", "sess-1", "cand-1", "pos-1"
    )
    assert shortlist_key == again
