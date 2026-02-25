"""
FastAPI server — exposes the HR Recruitment Agent as an HTTP API.

Start with:
    cd /Users/akarshgajbhiye/Documents/hr_ai
    uvicorn server:app --host 0.0.0.0 --port 8000 --reload

Or via start.sh (updated to expose both Streamlit + API).

Endpoints
---------
GET  /health                          — database + model connectivity check
POST /sessions                        — create a new isolated agent session
POST /sessions/{session_id}/chat      — send a chat turn, get response + tool calls
DELETE /sessions/{session_id}         — dispose session and release checkpointer slot
"""

from __future__ import annotations

import os
import time
import traceback
import uuid
from typing import Any

from dotenv import load_dotenv

load_dotenv()

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(
    title="HR Recruitment Agent API",
    description="Red-teamable HTTP interface for the HR Recruitment Agent",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory session registry
# ---------------------------------------------------------------------------
_sessions: dict[str, dict[str, Any]] = {}


def _get_session(session_id: str) -> dict[str, Any]:
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found. POST /sessions first.")
    return session


# ---------------------------------------------------------------------------
# Message extraction helpers (mirrors app.py logic)
# ---------------------------------------------------------------------------

def _msg_type(msg: Any) -> str:
    return str(getattr(msg, "type", getattr(msg, "role", "unknown")))


def _msg_text(msg: Any) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
            elif hasattr(block, "text"):
                parts.append(str(block.text))
        return "\n".join(parts)
    return str(content)


def _extract_tool_calls(msg: Any) -> list[dict[str, Any]]:
    raw = getattr(msg, "tool_calls", None) or []
    result = []
    for tc in raw:
        if isinstance(tc, dict):
            result.append({"name": tc.get("name", ""), "args": dict(tc.get("args", {}))})
        else:
            result.append({
                "name": str(getattr(tc, "name", "")),
                "args": dict(getattr(tc, "args", {})),
            })
    return result


MAX_TOOL_CALLS_PER_TURN = int(os.getenv("MAX_TOOL_CALLS_PER_TURN", "20"))


def _run_turn(agent: Any, session_id: str, message: str) -> dict[str, Any]:
    """Execute one chat turn synchronously and return response + tool calls."""
    from langchain_core.messages import HumanMessage

    tool_calls: list[dict[str, Any]] = []
    final_response = ""
    tool_call_count = 0
    budget_exhausted = False
    t0 = time.monotonic()

    thread_config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 50,
    }

    for chunk in agent.stream(
        {"messages": [HumanMessage(content=message)]},
        config=thread_config,
        stream_mode="values",
    ):
        messages = chunk.get("messages", []) if isinstance(chunk, dict) else []
        if not messages:
            continue
        last = messages[-1]
        mtype = _msg_type(last)

        if mtype == "ai":
            tcs = _extract_tool_calls(last)
            if tcs:
                tool_call_count += len(tcs)
                tool_calls.extend(tcs)
                if tool_call_count >= MAX_TOOL_CALLS_PER_TURN:
                    budget_exhausted = True
                    break
            else:
                text = _msg_text(last)
                if text:
                    final_response = text

    if budget_exhausted and not final_response:
        final_response = (
            f"Agent used all {MAX_TOOL_CALLS_PER_TURN} tool calls "
            "without producing a final response."
        )

    return {
        "response":   final_response,
        "tool_calls": tool_calls,
        "budget_exhausted": budget_exhausted,
        "duration_ms": int((time.monotonic() - t0) * 1000),
    }


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    client_id: str = Field(default="client-techcorp", description="Tenant ID")


class CreateSessionResponse(BaseModel):
    session_id: str
    client_id: str


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message to send to the agent")
    client_id: str = Field(default="client-techcorp", description="Must match session's client_id")


class ToolCallLog(BaseModel):
    name: str
    args: dict[str, Any]


class ChatResponse(BaseModel):
    session_id: str
    response: str
    tool_calls: list[ToolCallLog]
    duration_ms: int
    budget_exhausted: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _startup() -> None:
    """Run migrations + seed on startup."""
    try:
        from src.database.schema import run_migrations
        from src.database.seed import run_seed
        from src.database.db import get_db

        run_migrations()
        with get_db() as conn:
            rubric_count = conn.execute("SELECT COUNT(*) AS c FROM hiring_rubrics").fetchone()["c"]
        if rubric_count < 1:
            run_seed()
    except Exception as exc:
        print(f"[WARN] startup seed failed: {exc}")


@app.get("/health", summary="Database + model connectivity check")
async def health() -> dict[str, Any]:
    from src.health import run_health_check
    return run_health_check()


@app.post(
    "/sessions",
    response_model=CreateSessionResponse,
    summary="Create a new isolated agent session",
)
async def create_session(req: CreateSessionRequest = CreateSessionRequest()) -> CreateSessionResponse:
    from src.graph.workflow import build_agent
    from src.guardrails.rate_limiter import reset_session

    session_id = str(uuid.uuid4())
    reset_session(session_id)
    agent = build_agent(client_id=req.client_id, session_id=session_id)
    _sessions[session_id] = {"agent": agent, "client_id": req.client_id}
    return CreateSessionResponse(session_id=session_id, client_id=req.client_id)


@app.post(
    "/sessions/{session_id}/chat",
    response_model=ChatResponse,
    summary="Send a chat turn and receive agent response + tool call log",
)
async def chat(session_id: str, req: ChatRequest) -> ChatResponse:
    session = _get_session(session_id)
    agent   = session["agent"]
    error: str | None = None

    try:
        turn = _run_turn(agent, session_id, req.message)
    except Exception as exc:
        # If Postgres connection dropped, rebuild the agent and retry once
        if "SSL connection has been closed" in str(exc) or "consuming input failed" in str(exc):
            try:
                from src.database.db import reset_checkpointer
                from src.graph.workflow import build_agent
                reset_checkpointer()
                agent = build_agent(client_id=session["client_id"], session_id=session_id)
                session["agent"] = agent
                turn = _run_turn(agent, session_id, req.message)
            except Exception:
                turn = {"response": "", "tool_calls": [], "budget_exhausted": False, "duration_ms": 0}
                error = traceback.format_exc()
        else:
            turn = {"response": "", "tool_calls": [], "budget_exhausted": False, "duration_ms": 0}
            error = traceback.format_exc()

    return ChatResponse(
        session_id       = session_id,
        response         = turn["response"],
        tool_calls       = [ToolCallLog(**tc) for tc in turn["tool_calls"]],
        duration_ms      = turn["duration_ms"],
        budget_exhausted = turn["budget_exhausted"],
        error            = error,
    )


@app.delete(
    "/sessions/{session_id}",
    summary="Dispose a session and release its agent",
)
async def delete_session(session_id: str) -> dict[str, bool]:
    _sessions.pop(session_id, None)
    return {"deleted": True}


@app.get("/sessions", summary="List active session IDs")
async def list_sessions() -> dict[str, list[str]]:
    return {"session_ids": list(_sessions.keys())}
