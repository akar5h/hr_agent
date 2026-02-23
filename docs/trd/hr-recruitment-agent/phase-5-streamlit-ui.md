# Phase 5: Streamlit UI

## 1. Overview

Phase 5 builds a single `app.py` Streamlit application that provides a chat interface to the HR recruitment agent. Users select a client, upload resumes, provide candidate URLs, specify the position, and converse with the agent in a persistent chat.

The UI is intentionally minimal — one file, no authentication, no input sanitization — consistent with the project's red-teaming/security research purpose.

## 2. File Structure

```
hr_ai/
├── app.py                  # Single Streamlit application (NEW)
└── data/
    └── uploads/            # Resume upload directory (created at runtime)
```

Only one new file: `app.py` in the project root.

## 3. Full `app.py` Specification

### 3.1 Imports

```python
import streamlit as st
import uuid
import os
from pathlib import Path
from dotenv import load_dotenv
from src.database.schema import init_db
from src.graph.workflow import build_agent
from langchain_core.messages import HumanMessage, AIMessage

load_dotenv()
```

### 3.2 Page Config

```python
st.set_page_config(
    page_title="HR Recruitment Agent",
    page_icon="👔",
    layout="wide",
)
```

### 3.3 Session State Initialization

```python
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []  # List of {"role": "user"|"assistant", "content": str}
if "agent" not in st.session_state:
    st.session_state.agent = None
if "client_id" not in st.session_state:
    st.session_state.client_id = "client-techcorp"
```

### 3.4 Sidebar

```python
with st.sidebar:
    st.title("HR Recruitment Agent")
    st.markdown("---")

    # Client selector
    st.subheader("Client")
    client_id = st.selectbox(
        "Select Client",
        options=["client-techcorp", "client-startupai"],
        index=0,
        key="client_selector"
    )

    # Rebuild agent if client changes
    if client_id != st.session_state.client_id:
        st.session_state.client_id = client_id
        st.session_state.agent = None
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())

    st.markdown("---")

    # Candidate info section
    st.subheader("Candidate Input")

    # Resume upload
    uploaded_file = st.file_uploader(
        "Upload Resume (PDF or DOCX)",
        type=["pdf", "docx"],
        key="resume_uploader"
    )
    resume_path = None
    if uploaded_file:
        # Save to temp location
        temp_dir = Path("data/uploads")
        temp_dir.mkdir(parents=True, exist_ok=True)
        resume_path = temp_dir / uploaded_file.name
        with open(resume_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        st.success(f"Resume saved: {uploaded_file.name}")

    # LinkedIn URL
    linkedin_url = st.text_input(
        "LinkedIn URL",
        placeholder="https://linkedin.com/in/username",
        key="linkedin_input"
    )

    # Website URL
    website_url = st.text_input(
        "Personal Website",
        placeholder="https://candidate-site.com",
        key="website_input"
    )

    # Position
    position = st.text_input(
        "Position",
        placeholder="e.g., Senior Python Engineer",
        key="position_input"
    )

    st.markdown("---")

    # Evaluate button
    if st.button("Start Evaluation", type="primary", use_container_width=True):
        # Build evaluation prompt from sidebar inputs
        parts = ["Please evaluate this candidate."]
        if resume_path:
            parts.append(f"Resume file: {str(resume_path.absolute())}")
        if linkedin_url:
            parts.append(f"LinkedIn: {linkedin_url}")
        if website_url:
            parts.append(f"Website: {website_url}")
        if position:
            parts.append(f"Position: {position}")

        eval_prompt = " ".join(parts)
        st.session_state.pending_message = eval_prompt

    st.markdown("---")
    st.caption(f"Session: {st.session_state.session_id[:8]}...")
    st.caption(f"Client: {st.session_state.client_id}")
```

### 3.5 Main Area — Chat Interface

```python
st.title("HR Recruitment Agent")
st.markdown("Chat with the AI recruitment agent. Provide candidate details in the sidebar or type directly.")

# Initialize agent lazily
if st.session_state.agent is None:
    with st.spinner("Initializing agent..."):
        init_db()
        st.session_state.agent = build_agent(
            client_id=st.session_state.client_id,
            session_id=st.session_state.session_id,
        )

# Display message history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Handle sidebar "Start Evaluation" button
if "pending_message" in st.session_state:
    user_input = st.session_state.pop("pending_message")
    process_user_message(user_input)

# Chat input
user_input = st.chat_input("Ask about a candidate, request evaluation, or trigger ATS ranking...")
if user_input:
    process_user_message(user_input)
```

### 3.6 Message Processing

```python
def process_user_message(user_input: str):
    # Add user message to history
    st.session_state.messages.append({"role": "user", "content": user_input})

    # Display user message
    with st.chat_message("user"):
        st.markdown(user_input)

    # Stream agent response
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        full_response = ""

        try:
            for chunk in st.session_state.agent.stream(
                {"messages": [HumanMessage(content=user_input)]},
                config={"configurable": {"thread_id": st.session_state.session_id}},
                stream_mode="values"
            ):
                if "messages" in chunk:
                    last_msg = chunk["messages"][-1]
                    if hasattr(last_msg, "content") and isinstance(last_msg.content, str):
                        # Only update if this is the AI's response (not tool calls)
                        if last_msg.type == "ai" and not getattr(last_msg, "tool_calls", None):
                            full_response = last_msg.content
                            message_placeholder.markdown(full_response + "▌")

            message_placeholder.markdown(full_response)
        except Exception as e:
            full_response = f"Error: {str(e)}"
            message_placeholder.error(full_response)

        # Store response
        st.session_state.messages.append({"role": "assistant", "content": full_response})
```

### 3.7 Evaluation Report Display

```python
def render_evaluation_report(content: str):
    """If content has score patterns, render with st.metric widgets."""
    import re

    # Look for score patterns like "technical_score: 8.5" or "Technical: 8.5/10"
    score_pattern = re.compile(
        r'(technical|experience|culture|communication).*?(\d+\.?\d*)\s*(?:/\s*10)?',
        re.IGNORECASE
    )
    scores = score_pattern.findall(content)

    if scores:
        st.markdown("### Evaluation Scores")
        cols = st.columns(len(scores))
        for i, (dimension, score) in enumerate(scores):
            cols[i].metric(
                label=dimension.title(),
                value=f"{float(score):.1f}/10",
                delta=f"{float(score) - 5:.1f} vs avg"
            )

    # Render full reasoning as markdown
    st.markdown("### Evaluation Details")
    st.markdown(content)
```

## 4. UI Layout Mockup

```
┌─────────────────────┬──────────────────────────────────────────────┐
│ SIDEBAR             │ MAIN AREA                                     │
│                     │                                               │
│ HR Recruitment      │ HR Recruitment Agent                          │
│ Agent               │ ─────────────────────────────────────────     │
│ ─────────           │                                               │
│                     │ [user] Please evaluate Alice Chen             │
│ Client              │                                               │
│ [client-techcorp ▼] │ [assistant] I'll evaluate Alice Chen for      │
│                     │ the Senior Python Engineer position...        │
│ ─────────           │                                               │
│ Candidate Input     │ ### Evaluation Scores                        │
│                     │ ┌─────────┬──────────┬─────────┬──────────┐  │
│ [Upload Resume PDF] │ │Technical│Experience│Culture  │Comm      │  │
│                     │ │  8.5/10 │   7.0/10 │  7.5/10 │  8.0/10  │  │
│ LinkedIn URL        │ └─────────┴──────────┴─────────┴──────────┘  │
│ [________________]  │                                               │
│                     │ ### Evaluation Details                       │
│ Personal Website    │ Alice demonstrates strong Python expertise... │
│ [________________]  │                                               │
│                     │ ─────────────────────────────────────────     │
│ Position            │ [Ask about a candidate...              ] [→]  │
│ [________________]  │                                               │
│                     │                                               │
│ [Start Evaluation]  │                                               │
│                     │                                               │
│ ─────────           │                                               │
│ Session: a1b2c3...  │                                               │
│ Client: techcorp    │                                               │
└─────────────────────┴──────────────────────────────────────────────┘
```

## 5. Session State Schema

| Key | Type | Description |
|-----|------|-------------|
| `session_id` | `str` | UUID for this session (used as `thread_id` for LangGraph checkpointer) |
| `messages` | `list[dict]` | Chat history: `[{"role": "user"\|"assistant", "content": str}]` |
| `agent` | `CompiledGraph\|None` | LangGraph agent instance, rebuilt when client changes |
| `client_id` | `str` | Currently selected client (`"client-techcorp"` or `"client-startupai"`) |
| `pending_message` | `str` | Transient key set by "Start Evaluation" button, consumed on next rerun |

## 6. Environment Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Create .env from example
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY and TAVILY_API_KEY

# Initialize and seed database
python -c "from src.database.seed import run_seed; run_seed()"

# Run the Streamlit app
streamlit run app.py
```

### Required Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key for the LLM |
| `TAVILY_API_KEY` | Yes | Tavily API key for web search tool |

## 7. Intentional Vulnerabilities in UI

> **ATTACK SURFACE -- File Upload**: Uploaded files are saved to `data/uploads/` with the original filename. The absolute path is passed directly to the agent as part of the evaluation prompt. No filename sanitization is performed -- path traversal is possible if the agent constructs file paths from user input.

> **ATTACK SURFACE -- No Rate Limiting**: No message rate limiting or session timeout exists. Multi-turn exfiltration attacks (e.g., Eve Johnson's prompt injection chain) can proceed unimpeded across as many turns as needed.

> **ATTACK SURFACE -- Chat Input**: The `st.chat_input` value is passed directly to `agent.stream()` without any filtering or sanitization. Any text typed in the chat box becomes part of the LLM context, enabling direct prompt injection.

> **ATTACK SURFACE -- Client Selector**: The client dropdown is trusted without authentication. Any user can switch to any client and view/modify their candidates simply by selecting a different option from the dropdown. No authorization checks are performed.

## 8. Verification

```bash
# Start the app
streamlit run app.py

# Expected behavior:
# 1. Browser opens to localhost:8501
# 2. Sidebar shows client dropdown, file upload, URL inputs, position field
# 3. Chat interface loads with empty history
# 4. "Start Evaluation" button constructs a message from sidebar inputs
#    and sends it to the agent
# 5. Agent responds with evaluation (streamed to chat)
# 6. Switching clients resets session and rebuilds agent
```

### Manual Test Checklist

- [ ] App starts without errors on `streamlit run app.py`
- [ ] Client dropdown switches between `client-techcorp` and `client-startupai`
- [ ] Switching client clears chat history and generates new session ID
- [ ] File upload saves PDF/DOCX to `data/uploads/`
- [ ] "Start Evaluation" button sends constructed prompt to chat
- [ ] Agent responds with streamed output in chat
- [ ] Chat input field accepts free-form text and sends to agent
- [ ] Message history persists across reruns within same session
- [ ] Error states display gracefully (e.g., missing API keys)
