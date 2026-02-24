"""Main LangGraph recruiter workflow assembly."""

from __future__ import annotations

import json
from typing import Any

from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from src.database.db import get_checkpointer, get_db
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
    result = ats_agent.invoke(
        {"messages": [{"role": "user", "content": f"Rank all candidates for position {resolved_position_id}"}]},
        config={"configurable": {"thread_id": f"ats-{resolved_position_id}"}},
    )
    messages = result.get("messages", [])
    if not messages:
        return "ATS ranking completed with no output."
    return _extract_message_content(messages[-1])


AGENT_TOOLS = [*ALL_TOOLS, trigger_ats_ranking]


def build_agent(client_id: str = "default", session_id: str = "default"):
    """Build and return a compiled ReAct agent with all tools and checkpointing."""
    model = build_chat_model(temperature=0)

    prior_memories = _load_client_memories(client_id=client_id)
    system_prompt = build_system_prompt(
        client_id=client_id,
        session_id=session_id,
        prior_memories=prior_memories,
    )
    checkpointer = get_checkpointer()

    return create_react_agent(
        model=model,
        tools=AGENT_TOOLS,
        prompt=system_prompt,
        checkpointer=checkpointer,
    )
