"""Bridges DeepEval's ConversationSimulator to the in-process hr_ai agent.

``ConversationSimulator.simulate()`` calls its ``model_callback`` with ONLY the
user message string (no session/thread info) — so session identity has to be
bound via closure. ``make_model_callback`` returns one stateful callable per
scenario; run exactly ONE golden per scenario (per EXPERIMENT_PLAN.md / the
task instructions) so a fresh callable == a fresh hr_ai session.

Mirrors the turn-execution pattern in ``server.py::_run_turn`` (streamed
``agent.stream(..., stream_mode="values")`` over the compiled LangGraph agent),
but additionally captures per-turn ``usage_metadata`` for cost accounting and
records it into the shared ``cost_ledger``.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import HumanMessage

from runner import cost_ledger
from src.llm import DEFAULT_AGENT_MODEL

# .../experiments/historical_traffic_v0 — used to resolve upload artifact refs.
_EXPERIMENT_DIR = Path(__file__).resolve().parent.parent

# Same env var / default the agent itself uses (src/llm.py::build_chat_model).
BEDROCK_AGENT_MODEL = os.getenv("BEDROCK_AGENT_MODEL", DEFAULT_AGENT_MODEL)


def _msg_type(message: Any) -> str:
    return str(getattr(message, "type", getattr(message, "role", "unknown")))


# Cap on a single captured tool result. Tool observations (resume text, rubric JSON,
# scraped pages) can be large; a normal trace keeps them, but we bound each to keep the
# JSONL sane. Truncation is flagged, never silent.
_MAX_TOOL_RESULT_CHARS = 8000


def _extract_tool_calls(message: Any) -> list[dict[str, Any]]:
    raw = getattr(message, "tool_calls", None) or []
    result: list[dict[str, Any]] = []
    for tc in raw:
        if isinstance(tc, dict):
            result.append(
                {"name": tc.get("name", ""), "args": dict(tc.get("args", {})), "id": tc.get("id", "")}
            )
        else:
            result.append(
                {
                    "name": str(getattr(tc, "name", "")),
                    "args": dict(getattr(tc, "args", {})),
                    "id": str(getattr(tc, "id", "") or ""),
                }
            )
    return result


def _extract_tool_results(messages: list[Any], id_filter: set[str]) -> list[dict[str, Any]]:
    """Pull ToolMessage observations for the current turn.

    ``stream_mode="values"`` accumulates the whole checkpointed thread, so prior turns'
    ToolMessages are present too. Scope to this turn by joining on the tool_call ids the
    turn's AIMessages emitted (``id_filter``). If ids are unavailable, fall back to every
    ToolMessage (best effort). This is standard trace content — the tool observation —
    not a bespoke/scorer-shaped field.
    """
    results: list[dict[str, Any]] = []
    for msg in messages:
        if _msg_type(msg) != "tool":
            continue
        call_id = str(getattr(msg, "tool_call_id", "") or "")
        if id_filter and call_id not in id_filter:
            continue
        content = getattr(msg, "content", "")
        text = content if isinstance(content, str) else str(content)
        truncated = len(text) > _MAX_TOOL_RESULT_CHARS
        results.append(
            {
                "tool_call_id": call_id,
                "name": str(getattr(msg, "name", "") or ""),
                "result": text[:_MAX_TOOL_RESULT_CHARS],
                "truncated": truncated,
            }
        )
    return results


def _extract_usage(message: Any) -> dict[str, int]:
    usage = getattr(message, "usage_metadata", None) or {}
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class HrAgentBridge:
    """Stateful, per-scenario bridge exposing a ``Callable[[str], str]``.

    Instances are callable directly (``bridge(user_message)``), satisfying the
    ``model_callback: Callable[[str], str]`` contract DeepEval expects, while
    also exposing ``.events`` (normalized per-turn trace events) and
    ``.flush()`` (end-of-scenario Langfuse flush) for the caller.
    """

    def __init__(self, scenario: dict[str, Any], session_id: str, client_id: str) -> None:
        self.scenario = scenario
        self.session_id = session_id
        self.client_id = client_id
        self.events: list[dict[str, Any]] = []
        self.turn_index = 0
        self._agent: Any = None

    # -- lazy agent construction ---------------------------------------

    def _get_agent(self) -> Any:
        if self._agent is None:
            from src.graph.workflow import build_agent

            self._agent = build_agent(client_id=self.client_id, session_id=self.session_id)
        return self._agent

    # -- the DeepEval-facing callable ------------------------------------

    def __call__(self, input: str) -> "Turn":  # noqa: A002 (DeepEval requires the param name 'input')
        from deepeval.test_case import Turn  # lazy: keeps pure helpers importable without deepeval

        user_message = input
        self.turn_index += 1
        turn_no = self.turn_index

        self.events.append(
            {
                "turn": turn_no,
                "role": "user",
                "tool_calls": [],
                "tokens": {"input_tokens": 0, "output_tokens": 0},
                "ts": _now_iso(),
            }
        )

        send_message = self._augment_first_turn(user_message) if turn_no == 1 else user_message

        try:
            result = self._run_turn(send_message)
        except Exception as exc:
            # Surface the error as the assistant's "response" so the
            # ConversationSimulator can keep going / the scenario can be
            # flagged HARNESS_ERROR by the caller, but never crash the
            # simulator loop mid-conversation.
            error_text = f"[hr_bridge turn error] {type(exc).__name__}: {exc}"
            self.events.append(
                {
                    "turn": turn_no,
                    "role": "assistant",
                    "tool_calls": [],
                    "tokens": {"input_tokens": 0, "output_tokens": 0},
                    "ts": _now_iso(),
                    "error": error_text,
                }
            )
            raise

        cost_ledger.record_agent_usage(
            BEDROCK_AGENT_MODEL,
            result["usage"]["input_tokens"],
            result["usage"]["output_tokens"],
        )

        self.events.append(
            {
                "turn": turn_no,
                "role": "assistant",
                "tool_calls": result["tool_calls"],  # enriched: name/arguments/result_summary/status/error/latency_ms/cached
                "tokens": result["usage"],
                "ts": _now_iso(),
            }
        )

        return Turn(role="assistant", content=result["response"])

    # -- upload artifact injection (first turn of upload scenarios) ------

    def _augment_first_turn(self, message: str) -> str:
        """For upload-mode scenarios, surface the resume file path(s) to the
        agent on the first turn so its parse_resume tool has something to read
        (mimics a file upload; the simulated user's words stay separate)."""
        if str(self.scenario.get("input_mode", "")).lower() != "upload":
            return message
        refs = self.scenario.get("upload_artifact_refs") or []
        paths: list[str] = []
        for ref in refs:
            p = Path(str(ref))
            if not p.is_absolute():
                p = _EXPERIMENT_DIR / ref
            paths.append(str(p))
        if not paths:
            return message
        attach = " ".join(f"[Uploaded resume file: {p}]" for p in paths)
        return f"{message}\n\n{attach}"

    # -- core turn execution (mirrors server.py::_run_turn) -------------

    def _run_turn(self, message: str) -> dict[str, Any]:
        from src.graph.workflow import reset_turn_tool_cache
        from src.observability.tracing import get_trace_config

        agent = self._get_agent()
        reset_turn_tool_cache(self.session_id)

        case_id = str(self.scenario.get("case_id", "unknown"))
        trace_cfg = get_trace_config(
            session_id=self.session_id,
            user_id=self.client_id,
            tags=["preflight", "historical-traffic-v0", case_id],
            trace_name=f"preflight-{case_id}",
            workflow="Recruiter Chat",
            condition="preflight",
            graph_node="agent",
        )
        thread_config = {
            "configurable": {"thread_id": self.session_id},
            "recursion_limit": 50,
            **trace_cfg,
        }

        tool_calls: list[dict[str, Any]] = []
        final_response = ""
        usage_total = {"input_tokens": 0, "output_tokens": 0}
        last_messages: list[Any] = []
        t0 = time.monotonic()

        for chunk in agent.stream(
            {"messages": [HumanMessage(content=message)]},
            config=thread_config,
            stream_mode="values",
        ):
            messages = chunk.get("messages", []) if isinstance(chunk, dict) else []
            if not messages:
                continue
            last_messages = messages  # accumulated thread state; walked for tool results below
            last = messages[-1]
            if _msg_type(last) != "ai":
                continue

            tcs = _extract_tool_calls(last)
            if tcs:
                tool_calls.extend(tcs)

            usage = _extract_usage(last)
            usage_total["input_tokens"] += usage["input_tokens"]
            usage_total["output_tokens"] += usage["output_tokens"]

            content = getattr(last, "content", None)
            if isinstance(content, str) and content:
                final_response = content

        # Authoritative tool-call capture: the agent's _wrap_tool records each call with
        # full arguments, result_summary, status, error, latency_ms, cached (schema v2).
        # Fall back to message reconstruction (name+args+result only) if unavailable.
        structured: list[dict[str, Any]] = []
        try:
            from src.graph.workflow import get_turn_tool_calls

            structured = get_turn_tool_calls(self.session_id)
        except Exception:
            structured = []

        if structured:
            enriched = structured
        else:
            this_turn_ids = {tc["id"] for tc in tool_calls if tc.get("id")}
            res_by_id = {r["tool_call_id"]: r for r in _extract_tool_results(last_messages, this_turn_ids)}
            enriched = [
                {
                    "name": tc.get("name"),
                    "arguments": tc.get("args"),
                    "result_summary": (res_by_id.get(tc.get("id")) or {}).get("result"),
                    "status": "ok",
                    "error": None,
                    "latency_ms": None,
                    "cached": False,
                }
                for tc in tool_calls
            ]

        return {
            "response": final_response,
            "tool_calls": enriched,
            "usage": usage_total,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }

    # -- lifecycle --------------------------------------------------------

    def flush(self) -> None:
        """Flush the Langfuse client at end-of-scenario so spans land before
        the process may exit / move to the next scenario's fresh session.
        """
        try:
            from langfuse import get_client

            get_client().flush()
        except Exception:
            # Langfuse may be disabled (ENABLE_LANGFUSE=false) or not
            # importable in this environment; never let flushing crash a run.
            pass


def make_model_callback(
    scenario: dict[str, Any], session_id: str, client_id: str
) -> Callable[[str], str]:
    """Build a fresh, stateful ``Callable[[str], str]`` bound to one hr_ai
    session for one scenario. The returned callable also exposes ``.events``
    (list[dict]) and ``.flush()`` for the caller (see ``HrAgentBridge``).
    """
    return HrAgentBridge(scenario=scenario, session_id=session_id, client_id=client_id)
