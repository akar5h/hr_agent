"""Tests for the audit event writer."""

from __future__ import annotations

import logging

from src.guardrails import audit


def test_payload_hash_is_stable_and_order_invariant() -> None:
    a = audit._payload_hash({"position_id": "p1", "reason": "fit"})
    b = audit._payload_hash({"reason": "fit", "position_id": "p1"})
    assert a == b
    assert len(a) == 64
    assert audit._payload_hash(None) == ""


def test_payload_hash_differs_for_different_payloads() -> None:
    assert audit._payload_hash({"x": 1}) != audit._payload_hash({"x": 2})


def test_record_audit_event_swallows_db_failures(monkeypatch, caplog) -> None:
    class FakeConn:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("db down")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_get_db():
        return FakeConn()

    monkeypatch.setattr(audit, "get_db", fake_get_db)

    with caplog.at_level(logging.WARNING):
        audit.record_audit_event(
            client_id="client-X",
            tool="shortlist_candidate",
            action="shortlist",
            session_id="sess",
            target_id="cand-1",
            payload={"position_id": "pos-1"},
        )
    assert any("audit_event_write_failed" in r.message for r in caplog.records)
