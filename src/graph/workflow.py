"""Main LangGraph recruiter workflow assembly."""

from __future__ import annotations

import json
import os
from typing import Any

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from src.database.db import get_db
from src.graph.ats_subgraph import build_ats_agent
from src.prompts.evaluation import build_system_prompt
from src.tools import ALL_TOOLS
from src.tools._compat import tool


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
            "SELECT criteria FROM hiring_rubrics WHERE position_id = ? AND client_id = ?",
            (position_id, client_id),
        ).fetchone()

    if row is None:
        return "Rubric not found for the provided position and client."

    rubric = json.loads(row["criteria"]) if row["criteria"] else {}
    ats_agent = build_ats_agent(
        client_id=client_id,
        position_id=position_id,
        rubric=rubric,
    )
    result = ats_agent.invoke(
        {"messages": [{"role": "user", "content": f"Rank all candidates for position {position_id}"}]},
        config={"configurable": {"thread_id": f"ats-{position_id}"}},
    )
    messages = result.get("messages", [])
    if not messages:
        return "ATS ranking completed with no output."
    return _extract_message_content(messages[-1])


AGENT_TOOLS = [*ALL_TOOLS, trigger_ats_ranking]


def build_agent(client_id: str = "default", session_id: str = "default"):
    """Build and return a compiled ReAct agent with all tools and checkpointing."""
    model = ChatAnthropic(
        model="claude-sonnet-4-6",
        temperature=0,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
    )

    system_prompt = build_system_prompt(client_id=client_id, session_id=session_id)
    checkpointer = MemorySaver()

    return create_agent(
        model=model,
        tools=AGENT_TOOLS,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )

