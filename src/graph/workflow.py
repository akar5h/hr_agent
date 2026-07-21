"""Main LangGraph recruiter workflow assembly."""

from __future__ import annotations

import json
import hashlib
import os
import time
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from src.cache import session_dedup
from src.database.db import get_checkpointer, get_db
from src.observability.decorators import traced
from src.guardrails.rate_limiter import record_tool_call
from src.guardrails.sanitizer import ENABLE_HARDENING, sanitize
from src.guardrails.session_context import get_session_id, session_scope
from src.graph.ats_subgraph import build_ats_agent
from src.llm import (
    DEFAULT_OPENROUTER_MODEL,
    _model_supports_cache_control,
    build_chat_model,
    prompt_cache_enabled,
)
from src.prompts.evaluation import build_system_prompt, build_system_prompt_blocks
from src.tools import ALL_TOOLS
from src.tools._compat import tool
from src.tools.memory_tools import _load_client_memories


class TriggerATSRankingInput(BaseModel):
    """Input schema for trigger_ats_ranking."""

    position_id: str
    client_id: str


def _extract_message_content(message: Any) -> str:
    if hasattr(message, "content"):
        return str(message.content)
    if isinstance(message, dict):
        return str(message.get("content", ""))
    return str(message)


@tool(args_schema=TriggerATSRankingInput)
def trigger_ats_ranking(position_id: str, client_id: str) -> str:
    """Trigger the ATS sub-agent to rank all candidates for a position."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                hr.criteria,
                hr.position_id
            FROM hiring_rubrics hr
            JOIN positions p
              ON p.id = hr.position_id
             AND p.client_id = hr.client_id
            WHERE hr.client_id = %s
              AND (
                    hr.position_id = %s
                 OR LOWER(p.title) = LOWER(%s)
                 OR p.title ILIKE %s
              )
            ORDER BY
                CASE
                    WHEN hr.position_id = %s THEN 0
                    WHEN LOWER(p.title) = LOWER(%s) THEN 1
                    ELSE 2
                END,
                p.title ASC
            LIMIT 1
            """,
            (client_id, position_id, position_id, f"%{position_id}%", position_id, position_id),
        ).fetchone()

    if row is None:
        return "Rubric not found for the provided position and client."

    resolved_position_id = str(row["position_id"])
    rubric = json.loads(row["criteria"]) if row["criteria"] else {}
    ats_agent = build_ats_agent(
        client_id=client_id,
        position_id=resolved_position_id,
        rubric=rubric,
    )
    from src.observability.tracing import get_trace_config

    trace_cfg = get_trace_config(
        session_id=f"ats-{resolved_position_id}",
        tags=["ats-ranking", client_id],
        trace_name="ats-ranking",
    )
    ats_input = f"Rank all candidates for position {resolved_position_id}"
    result = ats_agent.invoke(
        {"messages": [{"role": "user", "content": ats_input}]},
        config={
            "configurable": {"thread_id": f"ats-{resolved_position_id}"},
            **trace_cfg,
        },
    )
    messages = result.get("messages", [])
    ranked_output = _extract_message_content(messages[-1]) if messages else "ATS ranking completed with no output."

    # Sub-agent span: model, input, ranked output, and which of the 4 steps
    # (fetch->score->rank->report) actually fired. Observability only.
    _ATS_STEPS = {
        "fetch_candidates_for_position": "fetch",
        "score_candidate": "score",
        "rank_candidates": "rank",
        "generate_ats_report": "report",
    }
    steps_fired: list[str] = []
    for _m in messages:
        for _tc in (getattr(_m, "tool_calls", None) or []):
            _n = _tc.get("name") if isinstance(_tc, dict) else getattr(_tc, "name", "")
            if _n in _ATS_STEPS and _ATS_STEPS[_n] not in steps_fired:
                steps_fired.append(_ATS_STEPS[_n])
    try:
        from src.observability.trace_capture import record_sub_agent

        record_sub_agent(
            get_session_id(),
            {
                "type": "ats_sub_agent",
                "tool": "trigger_ats_ranking",
                "model": os.getenv("BEDROCK_AGENT_MODEL", DEFAULT_OPENROUTER_MODEL),
                "position_id": resolved_position_id,
                "input": ats_input,
                "ranked_output": ranked_output[:2000],
                "steps_fired": steps_fired,
                "ran_all_4_steps": steps_fired == ["fetch", "score", "rank", "report"],
            },
        )
    except Exception:
        pass

    if not messages:
        return "ATS ranking completed with no output."
    return ranked_output


AGENT_TOOLS = [*ALL_TOOLS, trigger_ats_ranking]
PROMPT_VERSION = "candidate-screening-v2-reliability"
TOOL_VERSION = "tools-v2-reliability"
_DETERMINISTIC_TOOL_NAMES = {
    "parse_resume",
    "fetch_linkedin",
    "scrape_website",
    "search_web",
    "query_database",
    "get_candidate_by_email",
    "get_existing_evaluation",
    "get_hiring_rubric",
    "deduplicate_candidate",
    "retrieve_memory",
    "parallel_gather_candidate_info",
}
# Exact-duplicate tool calls (same tool + same args, same session) that keep
# recurring are a symptom of the agent looping — the per-turn deterministic
# cache above already short-circuits re-execution, but that's silent and the
# agent never learns it already has the answer. For this narrow tool set,
# repeats get a session-scoped (survives-turn-reset) stop-note prepended to
# the cached result instead of a plain silent cache hit.
_LOOP_NUDGE_TOOLS = {"query_database", "search_web"}
_LOOP_NUDGE_NOTE = (
    "You already made this exact tool call earlier in this session and got "
    "this result. Do NOT call it again — use the data you already have and "
    "proceed to the next step."
)
_TURN_TOOL_RESULTS: dict[str, dict[str, Any]] = {}

# Structured per-tool-call capture for the trace exporter. Populated by _wrap_tool,
# drained per turn by the traffic harness (hr_bridge). Observability only — never
# read by the agent, never changes a decision.
_TURN_TOOL_CALLS: dict[str, list[dict[str, Any]]] = {}

# resume_parser (and other) safety fields that must survive result truncation.
_RESULT_SAFETY_KEYS = (
    "truncated",
    "hidden_text_detected",
    "suspicious_instruction_detected",
    "warnings",
    "redaction_warnings",
)
_RESULT_SUMMARY_CAP = 2000


def _summarize_result(value: Any) -> Any:
    """Bounded, structure-preserving summary of a tool result for the trace.

    Small results pass through whole. Oversized results are truncated to a preview
    string, but any resume-safety fields are lifted out and preserved verbatim so
    downstream analysis never loses hidden-text / suspicious-instruction flags.
    """
    try:
        serialized = json.dumps(value, default=str)
    except Exception:
        serialized = str(value)
    if len(serialized) <= _RESULT_SUMMARY_CAP:
        return value
    summary: dict[str, Any] = {"_truncated": True, "_preview": serialized[:_RESULT_SUMMARY_CAP]}
    if isinstance(value, dict):
        for key in _RESULT_SAFETY_KEYS:
            if key in value:
                summary[key] = value[key]
    return summary


def reset_turn_tool_cache(session_id: str) -> None:
    """Clear per-turn deterministic tool results + captured tool calls for a session."""
    _TURN_TOOL_RESULTS.pop(session_id, None)
    _TURN_TOOL_CALLS.pop(session_id, None)


def get_turn_tool_calls(session_id: str) -> list[dict[str, Any]]:
    """Return the structured tool-call records captured for the current turn."""
    return list(_TURN_TOOL_CALLS.get(session_id, []))


def _tool_fingerprint(tool_name: str, kwargs: dict[str, Any]) -> str:
    serialized = json.dumps(kwargs, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{tool_name}:{digest}"


def _sanitize_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        return sanitize(payload)
    if isinstance(payload, dict):
        return {key: _sanitize_payload(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_sanitize_payload(item) for item in payload]
    return payload


def _sanitize_result(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, dict):
        return {key: _sanitize_result(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_result(item) for item in value]
    return value


def _wrap_tool(original_tool: Any, session_id: str, client_id: str = "default") -> Any:
    def _capture(name: str, args: Any, result: Any, status: str,
                 error: str | None, latency_ms: int, cached: bool) -> None:
        _TURN_TOOL_CALLS.setdefault(session_id, []).append(
            {
                "name": name,
                "arguments": args,
                "result_summary": _summarize_result(result) if result is not None else None,
                "status": status,
                "error": error,
                "latency_ms": latency_ms,
                "cached": cached,
            }
        )

    def wrapped_call(**kwargs: Any) -> Any:
        tool_name = getattr(original_tool, "name", "wrapped_tool")
        record_tool_call(session_id, tool_name)
        sanitized_input = _sanitize_payload(kwargs)
        fingerprint = _tool_fingerprint(tool_name, sanitized_input)

        if tool_name in _LOOP_NUDGE_TOOLS:
            dedup_key = f"wrap::{tool_name}::{fingerprint}"
            if session_dedup.seen(session_id, dedup_key):
                session_dedup.bump(session_id, dedup_key)
                prior = session_dedup.get(session_id, dedup_key)
                _capture(tool_name, sanitized_input, prior, "ok", None, 0, True)
                if isinstance(prior, list):
                    return [{"_dedup_note": _LOOP_NUDGE_NOTE}] + prior
                if isinstance(prior, dict):
                    return {"_dedup_note": _LOOP_NUDGE_NOTE, **prior}
                return [{"_dedup_note": _LOOP_NUDGE_NOTE}, {"result": prior}]

        if tool_name in _DETERMINISTIC_TOOL_NAMES:
            session_results = _TURN_TOOL_RESULTS.setdefault(session_id, {})
            if fingerprint in session_results:
                cached_result = session_results[fingerprint]
                _capture(tool_name, sanitized_input, cached_result, "ok", None, 0, True)
                return cached_result

        t0 = time.monotonic()
        try:
            with session_scope(client_id=client_id, session_id=session_id):
                result = original_tool.invoke(sanitized_input)
        except Exception as exc:
            _capture(tool_name, sanitized_input, None, "error", f"{type(exc).__name__}: {exc}",
                     int((time.monotonic() - t0) * 1000), False)
            raise
        latency_ms = int((time.monotonic() - t0) * 1000)
        sanitized_result = _sanitize_result(result)
        _capture(tool_name, sanitized_input, sanitized_result, "ok", None, latency_ms, False)
        if tool_name in _DETERMINISTIC_TOOL_NAMES:
            _TURN_TOOL_RESULTS.setdefault(session_id, {})[fingerprint] = sanitized_result
        if tool_name in _LOOP_NUDGE_TOOLS and session_id:
            session_dedup.put(session_id, f"wrap::{tool_name}::{fingerprint}", sanitized_result)
        return sanitized_result

    return StructuredTool.from_function(
        func=wrapped_call,
        name=getattr(original_tool, "name", "wrapped_tool"),
        description=getattr(original_tool, "description", ""),
        args_schema=getattr(original_tool, "args_schema", None),
        infer_schema=False,
        return_direct=getattr(original_tool, "return_direct", False),
    )


@traced(name="build-agent")
def build_agent(client_id: str = "default", session_id: str = "default"):
    """Build and return a compiled ReAct agent with all tools and checkpointing."""
    model = build_chat_model(temperature=0)
    from src.guardrails.nemo_integration import wrap_model_with_guardrails
    model = wrap_model_with_guardrails(model, session_client_id=client_id)

    prior_memories = _load_client_memories(
        client_id=client_id,
        query_context=f"session:{session_id}",
    )
    import os
    primary_model_name = os.getenv("BEDROCK_AGENT_MODEL", DEFAULT_OPENROUTER_MODEL)
    if prompt_cache_enabled() and _model_supports_cache_control(primary_model_name):
        system_prompt: Any = SystemMessage(
            content=build_system_prompt_blocks(
                client_id=client_id,
                session_id=session_id,
                prior_memories=prior_memories,
                model_name=primary_model_name,
            )
        )
    else:
        system_prompt = build_system_prompt(
            client_id=client_id,
            session_id=session_id,
            prior_memories=prior_memories,
        )
    checkpointer = get_checkpointer()
    tools_to_use = [_wrap_tool(tool, session_id, client_id) for tool in AGENT_TOOLS]

    return create_agent(
        model=model,
        tools=tools_to_use,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )
