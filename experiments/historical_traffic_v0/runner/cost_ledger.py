"""Process-wide Bedrock token ledger.

Two sources of usage feed this ledger:

1. Non-streaming ``Converse`` calls (the simulator model, via
   ``bedrock_sim.BedrockSimLLM``) — intercepted transparently by monkeypatching
   ``botocore.client.BaseClient._make_api_call`` and reading ``usage`` off the
   response for ``operation_name == "Converse"``.

2. Streaming agent turns (``agent.stream(...)`` over ``ConverseStream``) — the
   streaming response does NOT carry usage in the intercepted return value in a
   way we can reliably attribute per-call, so the agent side instead reports its
   own token counts explicitly via ``record_agent_usage(...)`` using the
   ``usage_metadata`` LangChain already attaches to the final AIMessage of each
   turn. This avoids double-counting: the patched ``_make_api_call`` only tallies
   ``Converse`` (used by the simulator client), never ``ConverseStream`` (used by
   the agent client), so the two sources are disjoint by construction.

Call ``install_ledger()`` once, early, before any Bedrock client is constructed
or used (order does not actually matter for botocore — the patch is applied to
the class, not an instance — but doing it first keeps the intent obvious).
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from runner.pricing import usd

_lock = threading.Lock()

# model_id -> {"in": int, "out": int, "calls": int}
_LEDGER: dict[str, dict[str, int]] = {}

_INSTALLED = False
_ORIGINAL_MAKE_API_CALL: Callable[..., Any] | None = None


def _tally(model: str, in_tok: int, out_tok: int) -> None:
    with _lock:
        row = _LEDGER.setdefault(model, {"in": 0, "out": 0, "calls": 0})
        row["in"] += int(in_tok)
        row["out"] += int(out_tok)
        row["calls"] += 1


def _patched_make_api_call(self: Any, operation_name: str, api_params: dict) -> Any:
    """Replacement for botocore.client.BaseClient._make_api_call.

    Delegates to the original implementation, then — for Bedrock Runtime
    ``Converse`` calls only — reads ``usage`` off the response and tallies it
    under the requested ``modelId``. ``ConverseStream`` is intentionally left
    untouched; the agent reports its own usage via ``record_agent_usage``.
    """
    assert _ORIGINAL_MAKE_API_CALL is not None
    response = _ORIGINAL_MAKE_API_CALL(self, operation_name, api_params)

    if operation_name == "Converse":
        try:
            model_id = api_params.get("modelId", "unknown")
            usage = response.get("usage") or {}
            in_tok = usage.get("inputTokens", 0)
            out_tok = usage.get("outputTokens", 0)
            if in_tok or out_tok:
                _tally(model_id, in_tok, out_tok)
        except Exception:
            # Never let ledger bookkeeping break a real Bedrock call.
            pass

    return response


def install_ledger() -> None:
    """Monkeypatch botocore to intercept Converse usage. Idempotent."""
    global _INSTALLED, _ORIGINAL_MAKE_API_CALL
    if _INSTALLED:
        return
    import botocore.client

    _ORIGINAL_MAKE_API_CALL = botocore.client.BaseClient._make_api_call
    botocore.client.BaseClient._make_api_call = _patched_make_api_call  # type: ignore[assignment]
    _INSTALLED = True


def uninstall_ledger() -> None:
    """Restore the original botocore method (mainly useful for tests)."""
    global _INSTALLED
    if not _INSTALLED or _ORIGINAL_MAKE_API_CALL is None:
        return
    import botocore.client

    botocore.client.BaseClient._make_api_call = _ORIGINAL_MAKE_API_CALL  # type: ignore[assignment]
    _INSTALLED = False


def record_agent_usage(model: str, in_tok: int, out_tok: int) -> None:
    """Record token usage for a streaming agent turn (ConverseStream), which
    the botocore patch cannot attribute reliably. Call once per agent turn
    with the ``usage_metadata`` counts from the final AIMessage.
    """
    _tally(model, in_tok, out_tok)


def snapshot() -> dict[str, dict[str, int]]:
    """Return a deep-copy snapshot of the ledger: {model: {in, out, calls}}."""
    with _lock:
        return {model: dict(counts) for model, counts in _LEDGER.items()}


def total_usd(since: dict[str, dict[str, int]] | None = None) -> float:
    """Total USD cost across all models currently in the ledger.

    If ``since`` is given (an earlier snapshot), returns only the cost of the
    delta accrued after that snapshot — useful for per-scenario cost.
    """
    current = snapshot()
    total = 0.0
    for model, counts in current.items():
        base = since.get(model, {"in": 0, "out": 0}) if since else {"in": 0, "out": 0}
        delta_in = max(0, counts["in"] - base.get("in", 0))
        delta_out = max(0, counts["out"] - base.get("out", 0))
        total += usd(model, delta_in, delta_out)
    return total


def delta_snapshot(
    before: dict[str, dict[str, int]],
) -> dict[str, dict[str, int]]:
    """Per-model {in, out, calls} accrued strictly after ``before`` was taken."""
    after = snapshot()
    delta: dict[str, dict[str, int]] = {}
    for model, counts in after.items():
        base = before.get(model, {"in": 0, "out": 0, "calls": 0})
        d_in = max(0, counts["in"] - base.get("in", 0))
        d_out = max(0, counts["out"] - base.get("out", 0))
        d_calls = max(0, counts["calls"] - base.get("calls", 0))
        if d_in or d_out or d_calls:
            delta[model] = {"in": d_in, "out": d_out, "calls": d_calls}
    return delta
