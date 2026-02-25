"""PostgreSQL connection and checkpoint management for the HR recruitment agent."""

from __future__ import annotations

import atexit
import os
from contextlib import ExitStack, contextmanager
from typing import Generator, Optional

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "")
# Backwards-compatible alias retained for older tests and callers.
DATABASE_PATH = DATABASE_URL
_CHECKPOINTER_STACK: Optional[ExitStack] = None
_CHECKPOINTER: Optional[PostgresSaver] = None


def _normalize_database_url(url: str) -> str:
    """Normalize SQLAlchemy-style postgres URLs to psycopg-compatible format."""
    normalized = url.strip()
    if normalized.startswith("postgresql+psycopg2://"):
        return "postgresql://" + normalized[len("postgresql+psycopg2://") :]
    if normalized.startswith("postgresql+psycopg://"):
        return "postgresql://" + normalized[len("postgresql+psycopg://") :]
    return normalized


def _resolve_database_url() -> str:
    # Prefer live environment values, then module-level compatibility aliases.
    url = os.getenv("DATABASE_URL", "") or DATABASE_URL or DATABASE_PATH
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return _normalize_database_url(url)


@contextmanager
def get_db() -> Generator[Connection, None, None]:
    """Yield a PostgreSQL connection configured for dict-like row access."""
    conn = Connection.connect(_resolve_database_url(), autocommit=False, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _close_checkpointer() -> None:
    global _CHECKPOINTER_STACK, _CHECKPOINTER
    if _CHECKPOINTER_STACK is not None:
        _CHECKPOINTER_STACK.close()
        _CHECKPOINTER_STACK = None
        _CHECKPOINTER = None


def reset_checkpointer() -> None:
    """Force-close and reset the singleton checkpointer."""
    _close_checkpointer()


def get_checkpointer() -> PostgresSaver:
    """Return a singleton Postgres-backed LangGraph checkpointer.

    If the underlying connection has been dropped (e.g. Neon idle timeout),
    the singleton is transparently recreated.
    """
    global _CHECKPOINTER_STACK, _CHECKPOINTER
    # Verify existing connection is still alive
    if _CHECKPOINTER is not None:
        try:
            _CHECKPOINTER.conn.execute("SELECT 1")
        except Exception:
            # Connection dead (SSL closed, idle timeout, etc.) — reset
            _close_checkpointer()

    if _CHECKPOINTER is None:
        stack = ExitStack()
        saver = stack.enter_context(PostgresSaver.from_conn_string(_resolve_database_url()))
        saver.setup()
        _CHECKPOINTER = saver
        _CHECKPOINTER_STACK = stack
        atexit.register(_close_checkpointer)
    return _CHECKPOINTER


def get_db_url() -> str:
    """Return the active database URL."""
    return _resolve_database_url()


def get_db_path() -> str:
    """Backwards-compatible alias for existing callers."""
    return get_db_url()
