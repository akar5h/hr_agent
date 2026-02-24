"""Unit tests for chat workflow action tools."""

from __future__ import annotations

import os

import pytest

from src.database.seed import run_seed
from src.tools.workflow_tools import reject_candidate, send_candidate_email, shortlist_candidate
from tests.unit.tools.utils import invoke_tool

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL required for Postgres workflow tool tests",
)


def _setup_seeded_db() -> None:
    run_seed()


def test_shortlist_candidate_happy_path() -> None:
    _setup_seeded_db()
    result = invoke_tool(
        shortlist_candidate,
        candidate_id="cand-alice-chen",
        position_id="pos-techcorp-spe",
        client_id="client-techcorp",
        reason="Strong systems experience",
        session_id="test-session-1",
    )
    assert result["success"] is True
    assert result["decision"] == "shortlist"


def test_reject_candidate_happy_path() -> None:
    _setup_seeded_db()
    result = invoke_tool(
        reject_candidate,
        candidate_id="cand-bob-martinez",
        position_id="pos-techcorp-spe",
        client_id="client-techcorp",
        reason="Insufficient depth",
        session_id="test-session-2",
    )
    assert result["success"] is True
    assert result["decision"] == "reject"


def test_send_candidate_email_happy_path() -> None:
    _setup_seeded_db()
    result = invoke_tool(
        send_candidate_email,
        candidate_id="cand-alice-chen",
        client_id="client-techcorp",
        subject="Interview Invitation",
        body="We would like to move forward to interviews.",
        session_id="test-session-3",
    )
    assert result["success"] is True
    assert result["status"] == "queued"
