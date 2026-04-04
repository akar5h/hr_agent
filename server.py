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
POST /sessions/{session_id}/evaluate  — upload resume + candidate info, trigger evaluation
POST /upload                          — upload a resume file (PDF/DOCX)
GET  /positions                       — list open positions for a client
GET  /positions/all                   — all positions with rubrics
GET  /history/uploads                 — uploaded entry log
GET  /history/queries                 — chat query history
"""

from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

UPLOADS_DIR = Path("data/uploads")
ENTRY_LOG_FILE = Path("data/upload_entries.jsonl")
QUERY_HISTORY_FILE = Path("data/query_history.jsonl")

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

    from src.observability.tracing import get_trace_callbacks

    callbacks = get_trace_callbacks(
        session_id=session_id,
        tags=["hr-agent", "api"],
        trace_name="hr-recruitment-api",
    )
    thread_config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 50,
        "callbacks": callbacks,
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
    from src.guardrails.nemo_integration import check_input, check_output

    session = _get_session(session_id)
    agent   = session["agent"]
    error: str | None = None

    # NeMo input rail check
    input_allowed, input_refusal = await check_input(req.message)
    if not input_allowed:
        return ChatResponse(
            session_id=session_id,
            response=input_refusal or "Request blocked by guardrails.",
            tool_calls=[],
            duration_ms=0,
            budget_exhausted=False,
            error=None,
        )

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

    # NeMo output rail check
    if turn["response"] and not error:
        output_allowed, output_replacement = await check_output(turn["response"])
        if not output_allowed:
            turn["response"] = output_replacement or "Response blocked by guardrails."

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


# ---------------------------------------------------------------------------
# File helpers (mirrors app.py)
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    entries: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append({k: str(v) if v is not None else "" for k, v in payload.items()})
    return entries


def _append_jsonl(path: Path, entry: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")


def _build_evaluation_prompt(
    resume_path: str | None,
    linkedin_url: str,
    website_url: str,
    position: str,
) -> str:
    parts = ["Please evaluate this candidate."]
    if resume_path:
        parts.append(f"Resume file: {resume_path}")
    if linkedin_url:
        parts.append(f"LinkedIn: {linkedin_url}")
    if website_url:
        parts.append(f"Website: {website_url}")
    if position:
        parts.append(f"Position: {position}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Upload endpoint
# ---------------------------------------------------------------------------

class UploadResponse(BaseModel):
    filename: str
    path: str


@app.post("/upload", response_model=UploadResponse, summary="Upload a resume file (PDF/DOCX)")
async def upload_resume(file: UploadFile = File(...)) -> UploadResponse:
    if file.filename is None:
        raise HTTPException(status_code=400, detail="No filename provided.")

    ext = Path(file.filename).suffix.lower()
    if ext not in (".pdf", ".docx"):
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{ext}'. Use PDF or DOCX.")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = UPLOADS_DIR / file.filename
    content = await file.read()
    output_path.write_bytes(content)
    return UploadResponse(filename=file.filename, path=str(output_path.resolve()))


# ---------------------------------------------------------------------------
# Evaluate endpoint (upload + candidate info → auto-chat)
# ---------------------------------------------------------------------------

class EvaluateRequest(BaseModel):
    resume_path: str | None = Field(default=None, description="Path to uploaded resume (from /upload)")
    linkedin_url: str = Field(default="", description="LinkedIn profile URL")
    website_url: str = Field(default="", description="Personal website URL")
    position: str = Field(default="", description="Position ID to evaluate against")


@app.post(
    "/sessions/{session_id}/evaluate",
    response_model=ChatResponse,
    summary="Trigger a full candidate evaluation",
)
async def evaluate(session_id: str, req: EvaluateRequest) -> ChatResponse:
    session = _get_session(session_id)
    agent = session["agent"]

    # Log the submission entry
    entry = {
        "entry_id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "client_id": session["client_id"],
        "session_id": session_id,
        "position": req.position.strip(),
        "resume_path": req.resume_path or "",
        "resume_file": Path(req.resume_path).name if req.resume_path else "",
        "linkedin_url": req.linkedin_url.strip(),
        "website_url": req.website_url.strip(),
    }
    _append_jsonl(ENTRY_LOG_FILE, entry)

    # Build prompt and run through the agent
    prompt = _build_evaluation_prompt(
        resume_path=req.resume_path,
        linkedin_url=req.linkedin_url,
        website_url=req.website_url,
        position=req.position,
    )

    error: str | None = None
    try:
        turn = _run_turn(agent, session_id, prompt)
    except Exception as exc:
        turn = {"response": "", "tool_calls": [], "budget_exhausted": False, "duration_ms": 0}
        error = traceback.format_exc()

    # Log query history
    query_entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "client_id": session["client_id"],
        "position_id": req.position,
        "user_input": prompt,
        "assistant_output": turn["response"],
    }
    _append_jsonl(QUERY_HISTORY_FILE, query_entry)

    return ChatResponse(
        session_id=session_id,
        response=turn["response"],
        tool_calls=[ToolCallLog(**tc) for tc in turn["tool_calls"]],
        duration_ms=turn["duration_ms"],
        budget_exhausted=turn["budget_exhausted"],
        error=error,
    )


# ---------------------------------------------------------------------------
# Positions endpoints
# ---------------------------------------------------------------------------

@app.get("/positions", summary="List open positions for a client")
async def list_positions(client_id: str = "client-techcorp") -> dict[str, Any]:
    from src.database.db import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, title FROM positions WHERE client_id = %s AND status = %s ORDER BY title ASC",
            (client_id, "open"),
        ).fetchall()
    return {
        "client_id": client_id,
        "positions": [{"id": str(row["id"]), "title": str(row["title"])} for row in rows],
    }


@app.get("/positions/all", summary="All positions with rubrics")
async def all_positions_with_rubrics() -> dict[str, Any]:
    from src.database.db import get_db

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                p.client_id,
                p.id AS position_id,
                p.title,
                p.status,
                hr.id AS rubric_id,
                hr.criteria
            FROM positions p
            LEFT JOIN hiring_rubrics hr
              ON hr.position_id = p.id
             AND hr.client_id = p.client_id
            ORDER BY p.client_id ASC, p.title ASC
            """
        ).fetchall()

    results = []
    for row in rows:
        results.append({
            "client_id": str(row["client_id"]),
            "position_id": str(row["position_id"]),
            "title": str(row["title"]),
            "status": str(row["status"]),
            "rubric_id": str(row["rubric_id"] or ""),
            "criteria": str(row["criteria"] or ""),
        })
    return {"positions": results}


# ---------------------------------------------------------------------------
# History endpoints
# ---------------------------------------------------------------------------

@app.get("/history/uploads", summary="Uploaded entry log")
async def upload_history() -> dict[str, Any]:
    entries = _load_jsonl(ENTRY_LOG_FILE)
    return {"total": len(entries), "entries": entries}


@app.get("/history/queries", summary="Chat query history")
async def query_history() -> dict[str, Any]:
    entries = _load_jsonl(QUERY_HISTORY_FILE)
    return {"total": len(entries), "queries": entries}
