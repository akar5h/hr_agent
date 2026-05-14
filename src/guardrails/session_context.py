"""Per-turn session context for tenant boundary enforcement.

The orchestrator (chat agent or workflow) sets the calling session's client_id
before invoking any tool. Tools consult `get_session_client_id()` to verify that
LLM-supplied client identifiers match the bound session and to reject cross-tenant
writes even if the model is tricked into supplying a different client_id.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_current_client_id: ContextVar[str | None] = ContextVar("current_client_id", default=None)
_current_session_id: ContextVar[str | None] = ContextVar("current_session_id", default=None)


def get_session_client_id() -> str | None:
    return _current_client_id.get()


def get_session_id() -> str | None:
    return _current_session_id.get()


@contextmanager
def session_scope(client_id: str, session_id: str = "") -> Iterator[None]:
    """Bind (client_id, session_id) for the duration of the block.

    Restores the previous values on exit, so nested invocations are safe.
    """
    client_token = _current_client_id.set(client_id)
    session_token = _current_session_id.set(session_id or None)
    try:
        yield
    finally:
        _current_client_id.reset(client_token)
        _current_session_id.reset(session_token)
