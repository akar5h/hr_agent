"""Streamlit UI for the HR Recruitment Agent."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Avoid protobuf C-extension incompatibilities in some local Python builds.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

import streamlit as st
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

load_dotenv()

UPLOADS_DIR = Path("data/uploads")
ENTRY_LOG_FILE = Path("data/upload_entries.jsonl")
QUERY_HISTORY_FILE = Path("data/query_history.jsonl")
CLIENT_OPTIONS = ["client-techcorp", "client-startupai"]
DEFAULT_POSITION_BY_CLIENT = {
    "client-techcorp": "pos-techcorp-spe",
    "client-startupai": "pos-startupai-mle",
}


def _configure_logger() -> logging.Logger:
    logger = logging.getLogger("hr_ai.app")
    if logger.handlers:
        return logger

    level_name = os.getenv("APP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    log_file = os.getenv("APP_LOG_FILE", "logs/backend.log").strip()
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


LOGGER = _configure_logger()


def _load_upload_entries() -> list[dict[str, str]]:
    if not ENTRY_LOG_FILE.exists():
        return []

    entries: list[dict[str, str]] = []
    for line in ENTRY_LOG_FILE.read_text(encoding="utf-8").splitlines():
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


def _append_upload_entry(entry: dict[str, str]) -> None:
    ENTRY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ENTRY_LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")


def _load_query_history() -> list[dict[str, str]]:
    if not QUERY_HISTORY_FILE.exists():
        return []

    entries: list[dict[str, str]] = []
    for line in QUERY_HISTORY_FILE.read_text(encoding="utf-8").splitlines():
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


def _append_query_history(entry: dict[str, str]) -> None:
    QUERY_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with QUERY_HISTORY_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")


def _ensure_db_seeded(log_events: bool = False) -> None:
    if st.session_state.db_ready:
        return

    from src.database.db import get_db
    from src.database.schema import run_migrations
    from src.database.seed import run_seed

    if log_events:
        _append_agent_log("agent_init: applying alembic migrations")
    run_migrations()
    with get_db() as conn:
        rubric_row = conn.execute("SELECT COUNT(*) AS count FROM hiring_rubrics").fetchone()
        position_row = conn.execute("SELECT COUNT(*) AS count FROM positions").fetchone()
    rubric_count = int(rubric_row["count"]) if rubric_row else 0
    position_count = int(position_row["count"]) if position_row else 0
    if rubric_count < 5 or position_count < 5:
        if log_events:
            _append_agent_log(
                "agent_init: positions/rubrics below baseline; running seed update "
                f"(positions={position_count}, rubrics={rubric_count})"
            )
        run_seed()
        if log_events:
            _append_agent_log("db_seeded: inserted default clients, positions, rubrics, candidates")
    st.session_state.db_ready = True


def _get_positions_for_client(client_id: str) -> list[dict[str, str]]:
    _ensure_db_seeded(log_events=False)
    from src.database.db import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, title FROM positions WHERE client_id = %s AND status = %s ORDER BY title ASC",
            (client_id, "open"),
        ).fetchall()

    return [{"id": str(row["id"]), "title": str(row["title"])} for row in rows]


def _get_all_positions_with_rubrics() -> list[dict[str, str]]:
    _ensure_db_seeded(log_events=False)
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

    results: list[dict[str, str]] = []
    for row in rows:
        criteria = row["criteria"] or ""
        results.append(
            {
                "client_id": str(row["client_id"]),
                "position_id": str(row["position_id"]),
                "title": str(row["title"]),
                "status": str(row["status"]),
                "rubric_id": str(row["rubric_id"] or ""),
                "criteria": str(criteria),
            }
        )
    return results


def _load_session_messages(session_id: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for row in st.session_state.query_history:
        if row.get("session_id") != session_id:
            continue
        user_input = row.get("user_input", "")
        assistant_output = row.get("assistant_output", "")
        if user_input:
            messages.append({"role": "user", "content": user_input})
        if assistant_output:
            messages.append({"role": "assistant", "content": assistant_output})
    return messages


def _open_session(session_id: str, client_id: str, position_id: str = "") -> None:
    st.session_state.session_id = session_id
    st.session_state.client_id = client_id
    st.session_state.position_input = position_id or st.session_state.get("position_input", "")
    st.session_state.messages = _load_session_messages(session_id)
    st.session_state.agent = None
    _append_agent_log(f"session_opened: session_id={session_id} client_id={client_id}")


def init_session_state() -> None:
    """Initialize Streamlit session state values used by the UI."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "agent" not in st.session_state:
        st.session_state.agent = None
    if "client_id" not in st.session_state:
        st.session_state.client_id = CLIENT_OPTIONS[0]
    if "position_input" not in st.session_state:
        st.session_state.position_input = DEFAULT_POSITION_BY_CLIENT[CLIENT_OPTIONS[0]]
    if "agent_logs" not in st.session_state:
        st.session_state.agent_logs = []
    if "show_agent_progress" not in st.session_state:
        st.session_state.show_agent_progress = True
    if "upload_entries" not in st.session_state:
        st.session_state.upload_entries = _load_upload_entries()
    if "db_ready" not in st.session_state:
        st.session_state.db_ready = False
    if "query_history" not in st.session_state:
        st.session_state.query_history = _load_query_history()


def _extract_text(content: Any) -> str:
    """Extract a readable text snippet from a LangChain message content payload."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
            elif hasattr(block, "text"):
                parts.append(str(block.text))
        return "\n".join(parts).strip()
    return str(content)


def _append_agent_log(message: str) -> str:
    """Append a timestamped log line to session log buffer and return it."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {message}"
    st.session_state.agent_logs.append(line)
    LOGGER.info(line)
    return line


def _message_type(message: Any) -> str:
    return str(getattr(message, "type", "unknown"))


def _tool_name_from_message(message: Any) -> str:
    return str(getattr(message, "name", "tool"))


def _format_tool_calls(message: Any) -> list[str]:
    tool_calls = getattr(message, "tool_calls", None) or []
    names: list[str] = []
    for call in tool_calls:
        if isinstance(call, dict):
            names.append(str(call.get("name", "tool")))
        else:
            names.append(str(getattr(call, "name", "tool")))
    return names


def _extract_latest_ai_text(messages: list[Any]) -> str:
    """Return most recent finalized AI text (no pending tool calls)."""
    for message in reversed(messages):
        if _message_type(message) != "ai":
            continue
        text = _extract_text(getattr(message, "content", ""))
        if not text:
            continue
        tool_calls = _format_tool_calls(message)
        if not tool_calls:
            return text
    return ""


# Patterns that indicate the model produced a preamble/thinking text instead of a final answer.
_INCOMPLETE_SIGNALS = [
    r"^now let me\b",
    r"^let me\b",
    r"^i (will|'ll|need to|should) (now |next |then )?\b(parse|fetch|scrape|search|evaluate|score)\b",
    r"^based on the (rubric|resume|profile),? i (will|'ll|now)\b",
    r":$",
]


def _looks_incomplete(text: str) -> bool:
    """Return True if the response appears to be a preamble rather than a final answer."""
    t = text.strip().lower()
    if len(t) < 150:
        for pattern in _INCOMPLETE_SIGNALS:
            if re.search(pattern, t, re.IGNORECASE | re.MULTILINE):
                return True
    return False


def save_uploaded_file(uploaded_file) -> Optional[Path]:
    """Persist uploaded resume to disk and return its absolute path."""
    if uploaded_file is None:
        return None

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = UPLOADS_DIR / uploaded_file.name
    with open(output_path, "wb") as handle:
        handle.write(uploaded_file.getbuffer())
    return output_path.resolve()


def _record_submission_entry(
    resume_path: Optional[Path],
    linkedin_url: str,
    website_url: str,
    position: str,
) -> None:
    entry = {
        "entry_id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "client_id": st.session_state.client_id,
        "session_id": st.session_state.session_id,
        "position": position.strip(),
        "resume_file": resume_path.name if resume_path is not None else "",
        "resume_path": str(resume_path) if resume_path is not None else "",
        "linkedin_url": linkedin_url.strip(),
        "website_url": website_url.strip(),
    }
    st.session_state.upload_entries.append(entry)
    _append_upload_entry(entry)
    _append_agent_log(
        "entry_logged: "
        f"position={entry['position'] or '[missing]'} resume={entry['resume_file'] or '[none]'} "
        f"website={entry['website_url'] or '[none]'}"
    )


def build_evaluation_prompt(
    resume_path: Optional[Path],
    linkedin_url: str,
    website_url: str,
    position: str,
) -> str:
    """Construct the evaluation instruction from sidebar inputs."""
    parts = ["Please evaluate this candidate."]
    if resume_path is not None:
        parts.append(f"Resume file: {resume_path}")
    if linkedin_url:
        parts.append(f"LinkedIn: {linkedin_url}")
    if website_url:
        parts.append(f"Website: {website_url}")
    if position:
        parts.append(f"Position: {position}")
    return " ".join(parts)


def get_or_create_agent(force_refresh: bool = False):
    """Build and cache the graph agent for the active client/session."""
    if force_refresh:
        st.session_state.agent = None

    if st.session_state.agent is None:
        from src.graph.workflow import build_agent

        with st.spinner("Initializing agent..."):
            _ensure_db_seeded(log_events=True)
            _append_agent_log(
                f"agent_init: building agent client={st.session_state.client_id} session={st.session_state.session_id}"
            )
            st.session_state.agent = build_agent(
                client_id=st.session_state.client_id,
                session_id=st.session_state.session_id,
            )
            _append_agent_log("agent_init: agent ready")
    return st.session_state.agent


def render_evaluation_report(content: str) -> None:
    """Render score metrics when score-like patterns are present."""
    score_pattern = re.compile(
        r"(technical|experience|culture|communication).*?(\d+\.?\d*)\s*(?:/\s*10)?",
        re.IGNORECASE,
    )
    scores = score_pattern.findall(content)

    if scores:
        st.markdown("### Evaluation Scores")
        columns = st.columns(len(scores))
        for idx, (dimension, score) in enumerate(scores):
            score_value = float(score)
            columns[idx].metric(
                label=dimension.title(),
                value=f"{score_value:.1f}/10",
                delta=f"{score_value - 5:.1f} vs avg",
            )

    st.markdown("### Evaluation Details")
    st.markdown(content)


def _render_assistant_content(content: str) -> None:
    if re.search(r"(technical|experience|culture|communication).*?\d+\.?\d*", content, re.IGNORECASE):
        render_evaluation_report(content)
    else:
        st.markdown(content)


def process_user_message(user_input: str) -> None:
    """Send a user message to the agent and stream its response."""
    st.session_state.messages.append({"role": "user", "content": user_input})
    history_item = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "session_id": st.session_state.session_id,
        "client_id": st.session_state.client_id,
        "position_id": st.session_state.get("position_input", ""),
        "user_input": user_input,
        "assistant_output": "",
    }

    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_response = ""
        last_messages: list[Any] = []

        for attempt in (1, 2):
            status_widget = None
            try:
                agent = get_or_create_agent(force_refresh=False)
                if st.session_state.show_agent_progress:
                    status_widget = st.status("Agent running...", expanded=True)

                _append_agent_log(f"user_input: {user_input[:120]} (attempt={attempt})")
                previous_chunk_key = None
                for chunk in agent.stream(
                    {"messages": [HumanMessage(content=user_input)]},
                    config={
                        "configurable": {"thread_id": st.session_state.session_id},
                        "recursion_limit": 25,
                    },
                    stream_mode="values",
                ):
                    if "messages" not in chunk or not chunk["messages"]:
                        continue

                    messages = chunk["messages"]
                    last_messages = messages
                    latest_ai_text = _extract_latest_ai_text(messages)
                    if latest_ai_text and latest_ai_text != full_response:
                        full_response = latest_ai_text
                        placeholder.markdown(full_response + "▌")
                        if status_widget is not None:
                            status_widget.update(label="Finalizing response...", state="running")

                    last_msg = messages[-1]
                    msg_type = _message_type(last_msg)
                    msg_text = _extract_text(getattr(last_msg, "content", ""))
                    tool_call_names = _format_tool_calls(last_msg)
                    chunk_key = f"{msg_type}|{msg_text[:120]}|{','.join(tool_call_names)}"
                    if chunk_key == previous_chunk_key:
                        continue
                    previous_chunk_key = chunk_key

                    if msg_type == "ai" and tool_call_names:
                        log_line = _append_agent_log(f"tool_call: {', '.join(tool_call_names)}")
                        if status_widget is not None:
                            status_widget.update(
                                label=f"Running tool: {', '.join(tool_call_names)}",
                                state="running",
                            )
                            status_widget.write(log_line)
                        continue

                    if msg_type == "tool":
                        tool_name = _tool_name_from_message(last_msg)
                        preview = msg_text.replace("\n", " ").strip()
                        if len(preview) > 140:
                            preview = preview[:140] + "..."
                        log_line = _append_agent_log(f"tool_result: {tool_name} -> {preview or '[empty]'}")
                        if status_widget is not None:
                            status_widget.write(log_line)
                        continue

                    if msg_type == "ai" and not tool_call_names:
                        full_response = msg_text
                        placeholder.markdown(full_response + "▌")
                        if status_widget is not None:
                            status_widget.update(label="Finalizing response...", state="running")

                if not full_response:
                    full_response = _extract_latest_ai_text(last_messages)
                if not full_response:
                    full_response = "No response generated."

                # If the model emitted only a thinking preamble and stopped, retry once.
                if attempt == 1 and _looks_incomplete(full_response):
                    _append_agent_log(
                        "warning: response looks incomplete; retrying with continuation prompt"
                    )
                    user_input = (
                        "Continue the evaluation — provide the full scoring and recommendation."
                    )
                    if status_widget is not None:
                        status_widget.update(label="Retrying continuation...", state="running")
                    continue

                placeholder.markdown("")
                _render_assistant_content(full_response)
                _append_agent_log("response_sent_to_ui")
                if status_widget is not None:
                    status_widget.update(label="Agent completed", state="complete")
                break
            except Exception as exc:
                error_text = str(exc)
                if attempt == 1 and "connection is closed" in error_text.lower():
                    _append_agent_log(
                        "warning: checkpointer connection closed; resetting and retrying once"
                    )
                    try:
                        from src.database import reset_checkpointer

                        reset_checkpointer()
                    except Exception as reset_exc:
                        _append_agent_log(f"warning: checkpointer reset failed: {reset_exc}")
                    st.session_state.agent = None
                    continue

                full_response = f"Error: {error_text}"
                placeholder.error(full_response)
                _append_agent_log(f"error: {exc}")
                if status_widget is not None:
                    status_widget.update(label="Agent failed", state="error")
                break

    st.session_state.messages.append({"role": "assistant", "content": full_response})
    history_item["assistant_output"] = full_response
    st.session_state.query_history.append(history_item)
    _append_query_history(history_item)


def _render_history_window() -> None:
    st.subheader("Sessions")
    sessions: dict[str, dict[str, str]] = {}
    for entry in st.session_state.upload_entries:
        sid = entry.get("session_id", "")
        if sid:
            sessions[sid] = {
                "session_id": sid,
                "client_id": entry.get("client_id", ""),
                "position": entry.get("position", ""),
                "timestamp": entry.get("timestamp", ""),
            }
    for row in st.session_state.query_history:
        sid = row.get("session_id", "")
        if sid and sid not in sessions:
            sessions[sid] = {
                "session_id": sid,
                "client_id": row.get("client_id", ""),
                "position": row.get("position_id", ""),
                "timestamp": row.get("timestamp", ""),
            }

    if sessions:
        ordered_sessions = sorted(
            sessions.values(),
            key=lambda item: item.get("timestamp", ""),
            reverse=True,
        )
        for session in ordered_sessions[:100]:
            col_text, col_btn = st.columns([5, 1])
            with col_text:
                st.markdown(
                    f"**{session.get('timestamp', '')}** | client: `{session.get('client_id', '')}` | "
                    f"position: `{session.get('position', '')}` | session: `{session.get('session_id', '')}`"
                )
            with col_btn:
                if st.button("Open", key=f"open-session-{session.get('session_id', '')}"):
                    _open_session(
                        session_id=session.get("session_id", ""),
                        client_id=session.get("client_id", ""),
                        position_id=session.get("position", ""),
                    )
                    st.success("Session loaded into chat. Scroll up to continue in chat.")
                    st.rerun()
    else:
        st.caption("No sessions found.")

    st.markdown("---")
    st.subheader("Uploaded Entry Packages")
    entries = st.session_state.upload_entries
    st.caption(f"Total entries: {len(entries)}")
    if entries:
        for entry in reversed(entries[-100:]):
            col_text, col_btn = st.columns([5, 1])
            with col_text:
                st.markdown(
                    f"**{entry.get('timestamp', '')}** | client: `{entry.get('client_id', '')}` | "
                    f"position: `{entry.get('position', '')}`"
                )
                st.code(
                    (
                        f"resume: {entry.get('resume_file', '') or '[none]'}\n"
                        f"resume_path: {entry.get('resume_path', '') or '[none]'}\n"
                        f"linkedin: {entry.get('linkedin_url', '') or '[none]'}\n"
                        f"website: {entry.get('website_url', '') or '[none]'}\n"
                        f"session_id: {entry.get('session_id', '')}"
                    ),
                    language="text",
                )
            with col_btn:
                if st.button("Open", key=f"open-entry-{entry.get('entry_id', '')}"):
                    _open_session(
                        session_id=entry.get("session_id", ""),
                        client_id=entry.get("client_id", ""),
                        position_id=entry.get("position", ""),
                    )
                    st.success("Entry session loaded into chat.")
                    st.rerun()
    else:
        st.caption("No uploaded entries yet.")

    st.markdown("---")
    st.subheader("Chat Query History")
    query_history = st.session_state.query_history
    st.caption(f"Total chat queries this session: {len(query_history)}")
    if query_history:
        for item in reversed(query_history[-100:]):
            st.markdown(
                f"**{item.get('timestamp', '')}** | client: `{item.get('client_id', '')}` | "
                f"position: `{item.get('position_id', '')}`"
            )
            st.code(
                (
                    f"user: {item.get('user_input', '')}\n\n"
                    f"assistant: {item.get('assistant_output', '')}\n\n"
                    f"session_id: {item.get('session_id', '')}"
                ),
                language="text",
            )
    else:
        st.caption("No chat queries yet.")


def _render_positions_window() -> None:
    st.subheader("All Positions")
    rows = _get_all_positions_with_rubrics()
    if not rows:
        st.caption("No positions found.")
        return
    st.caption(
        "You are currently scoped to one client in the sidebar. "
        "TechCorp has 3 open positions, StartupAI has 2 open positions."
    )
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_sidebar() -> None:
    with st.sidebar:
        st.title("HR Recruitment Agent")
        st.markdown("---")

        st.subheader("Client")
        current_index = CLIENT_OPTIONS.index(st.session_state.client_id)
        client_id = st.selectbox(
            "Select Client",
            options=CLIENT_OPTIONS,
            index=current_index,
            key="client_selector",
        )

        if client_id != st.session_state.client_id:
            st.session_state.client_id = client_id
            st.session_state.agent = None
            st.session_state.messages = []
            st.session_state.session_id = str(uuid.uuid4())
            st.session_state.agent_logs = []
            st.session_state.position_input = ""

        st.markdown("---")
        st.subheader("Candidate Input")

        uploaded_file = st.file_uploader(
            "Upload Resume (PDF or DOCX)",
            type=["pdf", "docx"],
            key="resume_uploader",
        )
        resume_path = save_uploaded_file(uploaded_file)
        if resume_path is not None:
            st.success(f"Resume saved: {uploaded_file.name}")

        linkedin_url = st.text_input(
            "LinkedIn URL",
            placeholder="https://linkedin.com/in/username",
            key="linkedin_input",
        )
        website_url = st.text_input(
            "Personal Website",
            placeholder="https://candidate-site.com",
            key="website_input",
        )
        positions = _get_positions_for_client(st.session_state.client_id)
        position_options = [item["id"] for item in positions]
        position_by_id = {item["id"]: item["title"] for item in positions}
        if not position_options:
            position = ""
            st.warning("No open positions found for this client.")
        else:
            if st.session_state.position_input not in position_options:
                st.session_state.position_input = DEFAULT_POSITION_BY_CLIENT.get(
                    st.session_state.client_id, position_options[0]
                )
                if st.session_state.position_input not in position_options:
                    st.session_state.position_input = position_options[0]

            position = st.selectbox(
                "Position ID",
                options=position_options,
                key="position_input",
                format_func=lambda pos_id: f"{pos_id} | {position_by_id.get(pos_id, '')}",
            )
            st.caption("Position IDs and titles are loaded from seeded database records.")

        st.markdown("---")
        if st.button("Start Evaluation", type="primary", use_container_width=True):
            _record_submission_entry(
                resume_path=resume_path,
                linkedin_url=linkedin_url,
                website_url=website_url,
                position=position,
            )
            st.session_state.pending_message = build_evaluation_prompt(
                resume_path=resume_path,
                linkedin_url=linkedin_url,
                website_url=website_url,
                position=position,
            )

        st.checkbox("Show live agent progress", key="show_agent_progress")
        if st.button("Clear agent logs", use_container_width=True):
            st.session_state.agent_logs = []

        with st.expander("Agent Logs", expanded=False):
            if st.session_state.agent_logs:
                for line in st.session_state.agent_logs[-200:]:
                    st.code(line)
            else:
                st.caption("No agent logs yet.")

        st.markdown("---")
        st.caption(f"Session: {st.session_state.session_id[:8]}...")
        st.caption(f"Client: {st.session_state.client_id}")
        if not os.getenv("OPENROUTER_API_KEY"):
            st.warning("OPENROUTER_API_KEY is not set.")
        st.caption(f"Model: {os.getenv('OPENROUTER_MODEL', 'deepseek/deepseek-v3.2')}")
        if not os.getenv("DATABASE_URL"):
            st.warning("DATABASE_URL is not set.")


def main() -> None:
    st.set_page_config(page_title="HR Recruitment Agent", page_icon=":briefcase:", layout="wide")
    init_session_state()
    _render_sidebar()

    col_chat, col_side = st.columns([1.9, 1.1], gap="large")

    with col_chat:
        st.title("HR Recruitment Agent")
        st.markdown(
            "Chat with the AI recruitment agent. Provide candidate details in the sidebar or type directly."
        )
        get_or_create_agent()

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                if message["role"] == "assistant":
                    _render_assistant_content(message["content"])
                else:
                    st.markdown(message["content"])

        if "pending_message" in st.session_state:
            pending_message = st.session_state.pop("pending_message")
            process_user_message(pending_message)

        user_input = st.chat_input("Ask about a candidate, request evaluation, or trigger ATS ranking...")
        if user_input:
            process_user_message(user_input)

    with col_side:
        history_tab, positions_tab = st.tabs(["History", "Positions"])
        with history_tab:
            _render_history_window()
        with positions_tab:
            _render_positions_window()


if __name__ == "__main__":
    main()
