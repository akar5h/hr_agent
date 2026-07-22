"""Unit tests for session-scoped tool-call dedup (src.cache.session_dedup)."""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager

from src.cache import session_dedup
from src.guardrails.session_context import session_scope
from src.tools.database_tools import query_database
from src.tools.web_search import search_web
from tests.unit.tools.utils import invoke_tool


# -- primitives ---------------------------------------------------------


def test_put_then_seen_and_get() -> None:
    session_dedup.reset("s1")
    assert session_dedup.seen("s1", "k1") is False
    session_dedup.put("s1", "k1", {"rows": [1]})
    assert session_dedup.seen("s1", "k1") is True
    assert session_dedup.get("s1", "k1") == {"rows": [1]}


def test_bump_increments_count() -> None:
    session_dedup.reset("s1")
    session_dedup.put("s1", "k1", {"rows": [1]})
    assert session_dedup.bump("s1", "k1") == 2
    assert session_dedup.bump("s1", "k1") == 3


def test_reset_clears_session() -> None:
    session_dedup.reset("s1")
    session_dedup.put("s1", "k1", {"rows": [1]})
    session_dedup.reset("s1")
    assert session_dedup.seen("s1", "k1") is False


def test_session_isolation() -> None:
    session_dedup.reset("s1")
    session_dedup.reset("s2")
    session_dedup.put("s1", "k1", {"rows": [1]})
    assert session_dedup.seen("s2", "k1") is False


def test_key_isolation() -> None:
    session_dedup.reset("s1")
    session_dedup.put("s1", "k1", {"rows": [1]})
    assert session_dedup.seen("s1", "k2") is False


def test_no_session_id_is_noop() -> None:
    assert session_dedup.seen(None, "k1") is False
    assert session_dedup.seen("", "k1") is False
    session_dedup.put(None, "k1", {"rows": [1]})
    assert session_dedup.get(None, "k1") is None
    assert session_dedup.bump("", "k1") == 0


# -- query_database dedup -------------------------------------------------


@contextmanager
def _fake_get_db(counter: list[int], rows: list[dict]):
    class _FakeConn:
        def execute(self, sql, *args, **kwargs):
            counter[0] += 1
            return self

        def fetchall(self):
            return [dict(r) for r in rows]

    yield _FakeConn()


def test_query_database_dedups_reworded_intents_same_sql(monkeypatch) -> None:
    session_dedup.reset("sess-a")
    from src.tools import database_tools

    monkeypatch.setattr(
        database_tools,
        "_generate_sql",
        lambda query_intent, client_id: "SELECT * FROM positions WHERE status='open'",
    )
    counter = [0]
    rows = [{"id": "pos-1", "title": "SPE"}]
    monkeypatch.setattr(
        database_tools, "get_db", lambda: _fake_get_db(counter, rows)
    )

    with session_scope(client_id="client-a", session_id="sess-a"):
        first = invoke_tool(
            query_database, query_intent="list all open positions", client_id="client-a"
        )
        second = invoke_tool(
            query_database,
            query_intent="get all positions with status 'open'",
            client_id="client-a",
        )

    assert counter[0] == 1, "DB should only be hit once for two reworded dupes"
    assert first == rows
    assert second[0] == {"_dedup_note": database_tools._DEDUP_NOTE}
    assert second[1:] == rows


def test_query_database_tenant_isolation_no_cross_client_hit(monkeypatch) -> None:
    session_dedup.reset("sess-b")
    from src.tools import database_tools

    monkeypatch.setattr(
        database_tools,
        "_generate_sql",
        lambda query_intent, client_id: "SELECT * FROM positions",
    )
    counter = [0]
    rows = [{"id": "pos-1"}]
    monkeypatch.setattr(
        database_tools, "get_db", lambda: _fake_get_db(counter, rows)
    )

    with session_scope(client_id="client-a", session_id="sess-b"):
        invoke_tool(query_database, query_intent="q", client_id="client-a")
    with session_scope(client_id="client-b", session_id="sess-b"):
        invoke_tool(query_database, query_intent="q", client_id="client-b")

    assert counter[0] == 2, "different client_id must not share a dedup hit"


def test_query_database_no_session_disables_dedup(monkeypatch) -> None:
    from src.tools import database_tools

    monkeypatch.setattr(
        database_tools,
        "_generate_sql",
        lambda query_intent, client_id: "SELECT * FROM positions",
    )
    counter = [0]
    rows = [{"id": "pos-1"}]
    monkeypatch.setattr(
        database_tools, "get_db", lambda: _fake_get_db(counter, rows)
    )

    # No session_scope bound -> get_session_id() returns None.
    first = invoke_tool(query_database, query_intent="q", client_id="client-a")
    second = invoke_tool(query_database, query_intent="q", client_id="client-a")

    assert counter[0] == 2
    assert first == rows
    assert second == rows


# -- search_web dedup -------------------------------------------------


def test_search_web_dedups_normalized_query(monkeypatch) -> None:
    session_dedup.reset("sess-c")
    monkeypatch.setenv("TAVILY_API_KEY", "fake-key")

    call_count = [0]

    class FakeTavilyClient:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def search(self, query: str, max_results: int):
            call_count[0] += 1
            return {
                "results": [
                    {
                        "title": "Priya",
                        "url": "https://example.com/priya",
                        "content": "Snippet",
                        "score": 0.9,
                    }
                ]
            }

    monkeypatch.setitem(sys.modules, "tavily", types.SimpleNamespace(TavilyClient=FakeTavilyClient))

    with session_scope(client_id="client-a", session_id="sess-c"):
        first = invoke_tool(search_web, query="Find Priya", max_results=3)
        second = invoke_tool(search_web, query="find   priya", max_results=3)

    assert call_count[0] == 1, "Tavily should only be called once for normalized-equal queries"
    assert first[0]["title"] == "Priya"
    assert "_dedup_note" in second[0]
    assert second[1]["title"] == "Priya"


def test_search_web_no_api_key_skips_dedup(monkeypatch) -> None:
    session_dedup.reset("sess-d")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    with session_scope(client_id="client-a", session_id="sess-d"):
        result = invoke_tool(search_web, query="anything", max_results=3)

    assert result == [{"error": "TAVILY_API_KEY not set"}]
