"""Main LangGraph recruiter workflow assembly."""

from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from src.database.db import get_checkpointer, get_db
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
    from src.observability.tracing import get_trace_callbacks

    ats_callbacks = get_trace_callbacks(
        session_id=f"ats-{resolved_position_id}",
        tags=["ats-ranking", client_id],
        trace_name="ats-ranking",
    )
    result = ats_agent.invoke(
        {"messages": [{"role": "user", "content": f"Rank all candidates for position {resolved_position_id}"}]},
        config={
            "configurable": {"thread_id": f"ats-{resolved_position_id}"},
            "callbacks": ats_callbacks,
        },
    )
    messages = result.get("messages", [])
    if not messages:
        return "ATS ranking completed with no output."
    return _extract_message_content(messages[-1])


AGENT_TOOLS = [*ALL_TOOLS, trigger_ats_ranking]


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


def _harden_tool(original_tool: Any, session_id: str) -> Any:
    def wrapped_call(**kwargs: Any) -> Any:
        record_tool_call(session_id)
        sanitized_input = _sanitize_payload(kwargs)
        result = original_tool.invoke(sanitized_input)
        return _sanitize_result(result)

    return StructuredTool.from_function(
        func=wrapped_call,
        name=getattr(original_tool, "name", "wrapped_tool"),
        description=getattr(original_tool, "description", ""),
        args_schema=getattr(original_tool, "args_schema", None),
        infer_schema=False,
        return_direct=getattr(original_tool, "return_direct", False),
    )


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
    tools_to_use = [_harden_tool(tool, session_id) for tool in AGENT_TOOLS] if ENABLE_HARDENING else AGENT_TOOLS

    return create_agent(
        model=model,
        tools=tools_to_use,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )
