"""Main LangGraph recruiter workflow assembly."""

from __future__ import annotations

import json
import hashlib
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from src.database.db import get_checkpointer, get_db
from src.observability.decorators import traced
from src.guardrails.rate_limiter import record_tool_call
from src.guardrails.sanitizer import ENABLE_HARDENING, sanitize
from src.graph.ats_subgraph import build_ats_agent
from src.llm import build_chat_model
from src.prompts.evaluation import build_system_prompt
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
    result = ats_agent.invoke(
        {"messages": [{"role": "user", "content": f"Rank all candidates for position {resolved_position_id}"}]},
        config={
            "configurable": {"thread_id": f"ats-{resolved_position_id}"},
            **trace_cfg,
        },
    )
    messages = result.get("messages", [])
    if not messages:
        return "ATS ranking completed with no output."
    return _extract_message_content(messages[-1])


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
_TURN_TOOL_RESULTS: dict[str, dict[str, Any]] = {}


def reset_turn_tool_cache(session_id: str) -> None:
    """Clear per-turn deterministic tool results for a session."""
    _TURN_TOOL_RESULTS.pop(session_id, None)


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


def _wrap_tool(original_tool: Any, session_id: str) -> Any:
    def wrapped_call(**kwargs: Any) -> Any:
        record_tool_call(session_id)
        sanitized_input = _sanitize_payload(kwargs)
        tool_name = getattr(original_tool, "name", "wrapped_tool")
        fingerprint = _tool_fingerprint(tool_name, sanitized_input)
        if tool_name in _DETERMINISTIC_TOOL_NAMES:
            session_results = _TURN_TOOL_RESULTS.setdefault(session_id, {})
            if fingerprint in session_results:
                return session_results[fingerprint]

        result = original_tool.invoke(sanitized_input)
        sanitized_result = _sanitize_result(result)
        if tool_name in _DETERMINISTIC_TOOL_NAMES:
            _TURN_TOOL_RESULTS.setdefault(session_id, {})[fingerprint] = sanitized_result
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
    system_prompt = build_system_prompt(
        client_id=client_id,
        session_id=session_id,
        prior_memories=prior_memories,
    )
    checkpointer = get_checkpointer()
    tools_to_use = [_wrap_tool(tool, session_id) for tool in AGENT_TOOLS]

    return create_agent(
        model=model,
        tools=tools_to_use,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )
