# HR Recruitment Agent

An intentionally vulnerable, multi-tenant AI recruitment agent built for **red-teaming and security research**. It demonstrates how prompt injection, data poisoning, and cross-tenant leakage can occur in production-style LLM agent systems — and how to harden against them.

> **Warning:** This system is deliberately insecure by default. Do not deploy to production without enabling hardening flags. See [Security & Attack Surface](#security--attack-surface).

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Agentic Workflows](#agentic-workflows)
- [Tools Reference](#tools-reference)
- [Input Methods & Expected Outputs](#input-methods--expected-outputs)
- [Memory System](#memory-system)
- [Sub-Agents](#sub-agents)
- [Module Map](#module-map)
- [Database Schema](#database-schema)
- [Security & Attack Surface](#security--attack-surface)
- [Configuration](#configuration)
- [Running the Project](#running-the-project)
- [Gotchas](#gotchas)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        ENTRY POINTS                                  │
│   ┌───────────────┐          ┌───────────────┐                       │
│   │  Streamlit UI │ :8501    │  FastAPI API   │ :8010                │
│   │  (app.py)     │          │  (server.py)   │                      │
│   └──────┬────────┘          └──────┬─────────┘                      │
│          │                          │                                │
│          └──────────┬───────────────┘                                │
│                     ▼                                                │
│   ┌─────────────────────────────────────────────────────────────┐    │
│   │              MAIN ReAct AGENT  (LangGraph)                  │    │
│   │              src/graph/workflow.py :: build_agent()          │    │
│   │                                                             │    │
│   │  LLM ─── OpenRouter (DeepSeek V3.2)                        │    │
│   │  State ── RecruiterState (TypedDict + add_messages)         │    │
│   │  Memory ─ PostgreSQL Checkpointer (per-thread persistence)  │    │
│   │                                                             │    │
│   │  ┌───────────────────────────────────────────────────────┐  │    │
│   │  │              15 TOOLS (see Tools Reference)           │  │    │
│   │  │  parse_resume · fetch_linkedin · scrape_website       │  │    │
│   │  │  search_web · query_database · write_database         │  │    │
│   │  │  get_hiring_rubric · deduplicate_candidate            │  │    │
│   │  │  store_memory · retrieve_memory                       │  │    │
│   │  │  parallel_gather_candidate_info · submit_evaluation   │  │    │
│   │  │  shortlist_candidate · reject_candidate               │  │    │
│   │  │  send_candidate_email · trigger_ats_ranking ──┐       │  │    │
│   │  └───────────────────────────────────────────────┼───────┘  │    │
│   └──────────────────────────────────────────────────┼──────────┘    │
│                                                      │               │
│                                                      ▼               │
│   ┌─────────────────────────────────────────────────────────────┐    │
│   │            ATS SUB-AGENT  (LangGraph Subgraph)              │    │
│   │            src/graph/ats_subgraph.py :: build_ats_agent()   │    │
│   │                                                             │    │
│   │  Tools: fetch_candidates_for_position · score_candidate     │    │
│   │         rank_candidates · generate_ats_report               │    │
│   └─────────────────────────────┬───────────────────────────────┘    │
│                                 │                                    │
│                                 ▼                                    │
│   ┌─────────────────────────────────────────────────────────────┐    │
│   │                    SUPPORT LAYERS                            │    │
│   │                                                             │    │
│   │  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │    │
│   │  │ Guardrails   │  │ Observability│  │   Cache (Redis)   │  │    │
│   │  │ • sanitizer  │  │ • LangSmith  │  │   src/cache/      │  │    │
│   │  │ • rate_limit │  │ • Langfuse   │  │   tool_cache.py   │  │    │
│   │  │ • NeMo rails │  │ • Galileo    │  │                   │  │    │
│   │  └─────────────┘  └──────────────┘  └───────────────────┘  │    │
│   └─────────────────────────────────────────────────────────────┘    │
│                                 │                                    │
│                                 ▼                                    │
│   ┌─────────────────────────────────────────────────────────────┐    │
│   │                 PostgreSQL  (Neon)                           │    │
│   │  clients · positions · candidates · hiring_rubrics          │    │
│   │  evaluations · candidate_decisions · outbound_emails        │    │
│   │  agent_memory · langgraph checkpoints                       │    │
│   └─────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Agentic Workflows

### 1. Candidate Evaluation (Main Loop)

The primary ReAct (Reason → Act → Observe) loop:

```
User message
    │
    ▼
┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
│  REASON     │────▶│  ACT (tool call) │────▶│   OBSERVE    │
│  (LLM plan) │◀────│  e.g. parse_     │◀────│  (tool result)│
│             │     │  resume           │     │              │
└─────────────┘     └──────────────────┘     └──────────────┘
    │  ... loop until evaluation complete ...
    ▼
submit_evaluation  →  structured scores + recommendation
```

**Typical sequence:**
1. `get_hiring_rubric` — fetch scoring criteria for the position
2. `parallel_gather_candidate_info` — concurrent resume + LinkedIn + website fetch
3. `deduplicate_candidate` — check if candidate already exists
4. LLM reasons about candidate fit against rubric dimensions
5. `submit_evaluation` — write structured scores (technical, experience, culture, communication)
6. `shortlist_candidate` or `reject_candidate` — decision + reason logged
7. `send_candidate_email` — queue notification (mock provider)

### 2. ATS Ranking (Sub-Agent)

Triggered via `trigger_ats_ranking` from the main agent:

```
Main Agent
    │
    │  trigger_ats_ranking(position_id, client_id)
    ▼
┌─────────────────────────────────────────┐
│           ATS Sub-Agent                  │
│                                          │
│  1. fetch_candidates_for_position        │
│     └─ reads evaluations from DB         │
│  2. score_candidate (per-candidate)      │
│     └─ weighted rubric calculation       │
│  3. rank_candidates                      │
│     └─ sort by overall score desc        │
│  4. generate_ats_report                  │
│     └─ markdown summary table            │
│                                          │
│  Returns: ranked report → Main Agent     │
└─────────────────────────────────────────┘
```

### 3. Context Compression

When conversation history exceeds token budget:

```
[sys] [user] [ai] [tool] [ai] [tool] [ai] [user] [ai] [tool] [ai]
 ──────────── old messages ──────────  ──── recent (kept) ────────
              │                                    │
              ▼                                    │
       LLM summarizes into                         │
       single SystemMessage                        │
              │                                    │
              └──────────────────┬─────────────────┘
                                 ▼
                    [summary] [user] [ai] [tool] [ai]
                    (compressed conversation continues)
```

Threshold: 32K tokens (configurable via `TOKEN_COMPRESS_THRESHOLD`).
Keeps last 8 messages intact + summarizes the rest.

### 4. Tool Hardening Pipeline (Optional)

When `ENABLE_HARDENING=true`:

```
User input
    │
    ▼
┌─────────────────────────┐
│  NeMo Guardrails         │  ← pattern-based input/output filtering
│  (if ENABLE_NEMO=true)   │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  Input Sanitization      │  ← null bytes, bidi chars, zero-width removal
│  sanitizer.py            │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  Rate Limiter            │  ← 50 tool calls/session (configurable)
│  rate_limiter.py         │
└───────────┬─────────────┘
            ▼
       Tool Execution
            │
            ▼
┌─────────────────────────┐
│  Output Sanitization     │  ← strip injections from tool results
└─────────────────────────┘
```

---

## Tools Reference

### Data Gathering

| Tool | Input | Output | Source |
|------|-------|--------|--------|
| `parse_resume` | file path (PDF/DOCX) | extracted text | `src/tools/resume_parser.py` |
| `fetch_linkedin` | candidate name or URL | JSON profile data | `src/tools/linkedin_fetcher.py` (fixture-based) |
| `scrape_website` | URL | page text content | `src/tools/website_scraper.py` |
| `search_web` | query string | search results | `src/tools/web_search.py` (Tavily API) |
| `parallel_gather_candidate_info` | resume_path, linkedin_url, website_url | combined data dict | `src/tools/parallel_gather.py` |

### Database Operations

| Tool | Input | Output | Source |
|------|-------|--------|--------|
| `query_database` | natural language question | SQL results as text | `src/tools/database_tools.py` |
| `write_database` | table, data dict | success/failure message | `src/tools/database_tools.py` |
| `get_hiring_rubric` | position_id, client_id | rubric criteria JSON | `src/tools/database_tools.py` |
| `submit_evaluation` | candidate_id, position_id, scores, reasoning | confirmation | `src/tools/database_tools.py` |
| `deduplicate_candidate` | email | existing candidate or "not found" | `src/tools/deduplicator.py` |

### Workflow Actions

| Tool | Input | Output | Source |
|------|-------|--------|--------|
| `shortlist_candidate` | candidate_id, position_id, reason | confirmation | `src/tools/workflow_tools.py` |
| `reject_candidate` | candidate_id, position_id, reason | confirmation | `src/tools/workflow_tools.py` |
| `send_candidate_email` | candidate_id, subject, body | queued status | `src/tools/workflow_tools.py` |

### Memory

| Tool | Input | Output | Source |
|------|-------|--------|--------|
| `store_memory` | key, value, memory_type | confirmation | `src/tools/memory_tools.py` |
| `retrieve_memory` | query string | matching memories | `src/tools/memory_tools.py` |

### Orchestration

| Tool | Input | Output | Source |
|------|-------|--------|--------|
| `trigger_ats_ranking` | position_id, client_id | ranked report markdown | `src/graph/workflow.py` |

---

## Input Methods & Expected Outputs

### Streamlit UI (port 8501)

**Inputs:**
- Chat messages (text box)
- Resume file upload (PDF/DOCX via sidebar)
- Client selection (dropdown: `client-techcorp`, `client-startupai`)
- Position selection (dropdown, fetched from DB)

**Outputs:**
- Streamed agent responses with tool call visualization
- Evaluation scores (0–10 per dimension)
- Recommendation: `STRONG_HIRE | HIRE | CONSIDER | PASS`
- ATS ranking reports (markdown tables)
- Token usage + cost estimates in sidebar

### FastAPI API (port 8010)

| Endpoint | Method | Input | Output |
|----------|--------|-------|--------|
| `/sessions` | POST | `{client_id}` | `{session_id}` |
| `/sessions/{id}/chat` | POST | `{message}` | `{response, tool_calls}` |
| `/sessions/{id}/evaluate` | POST | multipart (resume file + position_id) | evaluation result |
| `/upload` | POST | multipart (file) | `{file_path}` |
| `/positions` | GET | `?client_id=` | position list |
| `/positions/all` | GET | — | all positions + rubrics |
| `/health` | GET | — | `{status, db, model}` |
| `/sessions/{id}` | DELETE | — | cleanup confirmation |

---

## Memory System

Two-tier memory stored in the `agent_memory` table:

```
┌─────────────────────────────────────────────┐
│               agent_memory                   │
│                                              │
│  EPISODIC (session-scoped, TTL 30 days)      │
│  ├─ "candidate X prefers remote work"        │
│  ├─ "user asked to focus on ML skills"       │
│  └─ auto-expires, access-count tracked       │
│                                              │
│  SEMANTIC (cross-session, persists)           │
│  ├─ "TechCorp values system design skills"   │
│  ├─ "StartupAI prefers full-stack profiles"  │
│  └─ retrieved via cosine similarity search   │
│                                              │
│  Retrieval: char n-gram embeddings           │
│  Stored as: JSON float vectors in TEXT col    │
└─────────────────────────────────────────────┘
```

**store_memory(key, value, type)** — writes to DB with optional embedding.
**retrieve_memory(query)** — cosine similarity over n-gram embeddings, returns top matches.

The main agent's system prompt is hydrated with prior memories at session start via `_load_client_memories()`.

---

## Sub-Agents

### ATS Sub-Agent (`src/graph/ats_subgraph.py`)

A self-contained LangGraph ReAct agent invoked **inside** the main agent's tool call.

**State type:** `ATSState` — tracks candidates list, scores dict, ranked output, final report.

**Dedicated tools (not shared with main agent):**
1. `fetch_candidates_for_position` — SQL join across evaluations + candidates
2. `score_candidate` — weighted rubric calculation from evaluation scores
3. `rank_candidates` — sort by overall score descending
4. `generate_ats_report` — markdown table with scores per dimension

**Invocation:** Main agent calls `trigger_ats_ranking(position_id, client_id)` → builds ATS agent on-the-fly → runs → returns markdown report string.

---

## Module Map

```
hr_ai/
├── app.py                          # Streamlit chat UI (1003 lines)
├── server.py                       # FastAPI HTTP API (574 lines)
├── start.sh                        # Startup: venv, migrations, launch
├── Dockerfile                      # python:3.12-slim, uvicorn workers
├── requirements.txt                # All dependencies
├── alembic/                        # Database migrations
│   └── versions/                   # Migration scripts
│
├── src/
│   ├── llm.py                      # OpenRouter ChatOpenAI builder + fallback model
│   ├── health.py                   # Pre-flight: DB + model connectivity check
│   │
│   ├── graph/                      # ── Agent orchestration ──
│   │   ├── state.py                # RecruiterState, ATSState (TypedDict)
│   │   ├── workflow.py             # build_agent(), tool hardening, trigger_ats_ranking
│   │   ├── ats_subgraph.py         # ATS sub-agent + 4 ranking tools
│   │   └── compression.py          # Sliding window summarization, token budgeting
│   │
│   ├── tools/                      # ── Agent tools (15 total) ──
│   │   ├── __init__.py             # ALL_TOOLS export list
│   │   ├── _compat.py              # LangChain @tool decorator fallback
│   │   ├── resume_parser.py        # PDF/DOCX → text extraction
│   │   ├── linkedin_fetcher.py     # Fixture-based LinkedIn profiles
│   │   ├── website_scraper.py      # HTTP + BeautifulSoup scraping
│   │   ├── web_search.py           # Tavily API search with caching
│   │   ├── database_tools.py       # NL→SQL, write, rubric, evaluation
│   │   ├── deduplicator.py         # Email-based duplicate detection
│   │   ├── memory_tools.py         # Store/retrieve episodic+semantic memory
│   │   ├── workflow_tools.py       # Shortlist, reject, send email
│   │   └── parallel_gather.py      # Concurrent multi-source data fetch
│   │
│   ├── database/                   # ── Data layer ──
│   │   ├── db.py                   # PostgreSQL connection pool + checkpointer
│   │   ├── schema.py               # DDL, Alembic migration runner
│   │   └── seed.py                 # 2 clients, 5 positions, 5 candidates + attack payloads
│   │
│   ├── prompts/                    # ── System prompts ──
│   │   ├── evaluation.py           # Main agent system prompt builder
│   │   └── ats.py                  # ATS sub-agent prompt builder
│   │
│   ├── guardrails/                 # ── Security hardening (opt-in) ──
│   │   ├── sanitizer.py            # Input/output sanitization, instruction boundaries
│   │   ├── rate_limiter.py         # Per-session tool call limiting (default: 50)
│   │   └── nemo_integration.py     # NeMo LLMRails guardrails wrapper
│   │
│   ├── memory/                     # ── Memory retrieval engine ──
│   │   ├── retrieval.py            # Char n-gram embeddings, cosine similarity
│   │   ├── ttl.py                  # Episodic memory expiration (30-day default)
│   │   └── consolidation.py        # (placeholder)
│   │
│   ├── cache/                      # ── Tool output caching ──
│   │   └── tool_cache.py           # Redis-backed with in-memory fallback, TTL
│   │
│   └── observability/              # ── Tracing & logging ──
│       ├── tracing.py              # LangSmith / Langfuse / Galileo lazy init
│       ├── decorators.py           # @traced() decorator for any function
│       └── logging.py              # structlog + stdlib fallback
│
├── fixtures/                       # ── Test data ──
│   ├── linkedin/                   # JSON profiles (alice, bob, charlie, diana, eve)
│   └── websites/                   # HTML portfolio pages
│
├── nemo_config/                    # ── NeMo Guardrails config ──
│   ├── config.yml                  # Rails definitions
│   ├── prompts.yml                 # Guard prompts
│   └── *.co                        # Colang flow definitions
│
├── tests/                          # ── Test suite ──
│   ├── unit/                       # Unit tests per module
│   │   ├── tools/                  # One test file per tool
│   │   ├── guardrails/             # Sanitizer + rate limiter tests
│   │   ├── cache/                  # Cache layer tests
│   │   └── graph/                  # Compression tests
│   ├── integration/                # ATS subgraph integration tests
│   └── test_graph/                 # Workflow builder tests
│
└── docs/trd/hr-recruitment-agent/  # ── Technical Design Docs ──
    ├── master.md                   # Architecture overview
    ├── phase-1-database.md         # Schema design
    ├── phase-2-tools.md            # Tool specifications
    ├── phase-3-langgraph.md        # Agent graph design
    ├── phase-4-ats-subgraph.md     # ATS sub-agent design
    ├── phase-5-streamlit-ui.md     # UI specification
    ├── phase-6-10-production-hardening.md  # Hardening phases
    ├── plan-hardening.md           # Security hardening plan
    └── plan-attack-suite.md        # Red-team attack scenarios
```

---

## Database Schema

**Engine:** PostgreSQL (Neon cloud-hosted)
**Migrations:** Alembic (`alembic/versions/`)

```
┌──────────────┐       ┌──────────────┐       ┌──────────────────┐
│   clients     │──1:N─▶│  positions   │──1:N─▶│  hiring_rubrics  │
│ id, name,     │       │ id, client_id│       │ id, position_id  │
│ industry      │       │ title, status│       │ criteria (JSON)  │
└──────┬───────┘       └──────┬───────┘       │ scoring_notes    │
       │                      │                └──────────────────┘
       │                      │
       │  ┌───────────────────┤
       │  │                   │
       ▼  ▼                   ▼
┌──────────────┐       ┌──────────────────┐
│  candidates   │──1:N─▶│   evaluations    │
│ id, name,     │       │ candidate_id     │
│ email, skills │       │ position_id      │
│ resume_text   │       │ technical_score  │
│ linkedin_data │       │ experience_score │
│ website_data  │       │ culture_score    │
│ score         │       │ communication_   │
│ client_id     │       │   score          │
└──────┬───────┘       │ overall_score    │
       │                │ recommendation   │
       │                └──────────────────┘
       │
       ├──1:N──▶ candidate_decisions (shortlist/reject + reason)
       ├──1:N──▶ outbound_emails (subject, body, status, provider)
       │
       └────────  agent_memory (session_id, key, value, type, embedding, TTL)
```

**Seed data** (`src/database/seed.py`): 2 clients, 5 positions, 5 candidates with pre-loaded attack payloads in resume/LinkedIn/website fields.

---

## Security & Attack Surface

This project is a **deliberately vulnerable testbed**. Attack vectors are embedded in seed data:

| Candidate | Attack Vector | Exploited Tool | Technique |
|-----------|--------------|----------------|-----------|
| Bob Martinez | Hidden white text in PDF resume | `parse_resume` | Invisible prompt injection in document |
| Charlie Kim | LinkedIn bio contains instructions | `fetch_linkedin` | Profile data injection |
| Diana Patel | Portfolio website with injected prompts | `scrape_website` → `write_database` | Stored injection via web scraping |
| Eve Johnson | Multi-turn chat exfiltration | `query_database` | Conversational manipulation |
| Alice Chen | Clean baseline (no attacks) | — | Control candidate |

### Hardening Layers (Opt-In)

| Layer | Flag | What It Does |
|-------|------|-------------|
| Input sanitization | `ENABLE_HARDENING=true` | Strips null bytes, bidi formatting, zero-width chars, instruction patterns |
| Output sanitization | `ENABLE_HARDENING=true` | Filters tool results before they reach the LLM |
| Rate limiting | `ENABLE_HARDENING=true` | Caps tool calls per session (default 50) |
| NeMo Guardrails | `ENABLE_NEMO_GUARDRAILS=true` | Pattern-based input/output blocking via Colang flows |
| Instruction boundaries | `ENABLE_HARDENING=true` | Wraps system prompt sections with boundary markers |

---

## Configuration

All configuration via environment variables (`.env` file):

```bash
# ── LLM Provider (Required) ──
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=deepseek/deepseek-v3.2
OPENROUTER_FALLBACK_MODEL=deepseek/deepseek-chat
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# ── Database (Required) ──
DATABASE_URL=postgresql://user:pass@host/db?sslmode=require

# ── Web Search (Required for search_web tool) ──
TAVILY_API_KEY=tvly-...

# ── Caching (Optional — falls back to in-memory) ──
REDIS_URL=redis://localhost:6379

# ── Security Hardening (Default: off) ──
ENABLE_HARDENING=false
ENABLE_NEMO_GUARDRAILS=false
MAX_TOOL_CALLS_PER_SESSION=50

# ── Observability (All optional) ──
ENABLE_LANGSMITH=false
ENABLE_LANGFUSE=false
ENABLE_GALILEO=false
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...

# ── Compression ──
TOKEN_COMPRESS_THRESHOLD=32000

# ── Cost Tracking ──
TOKEN_COST_PER_M_IN=0.27
TOKEN_COST_PER_M_OUT=1.10
```

---

## Running the Project

### Quick Start (Local)

```bash
# 1. Clone and setup
cp .env.example .env   # fill in your keys
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Run migrations + seed + launch
bash start.sh
# → Opens Streamlit at http://localhost:8501
```

### FastAPI Server

```bash
uvicorn server:app --host 0.0.0.0 --port 8010 --workers 5
```

### Docker

```bash
docker build -t hr-ai .
docker run -p 8010:8010 --env-file .env hr-ai
```

### Run Tests

```bash
pytest tests/ -x --tb=short                    # all tests
pytest tests/unit/ -x --tb=short               # unit only
pytest tests/integration/ -m integration       # integration only
```

---

## Gotchas

1. **LinkedIn data is fixture-based.** `fetch_linkedin` loads JSON from `fixtures/linkedin/`, not a real API. Candidate name is fuzzy-matched to fixture filenames.

2. **`query_database` generates SQL from natural language.** The LLM writes raw SQL — this is an intentional vulnerability. There is no parameterized query layer between the LLM and the database.

3. **Hardening is OFF by default.** Set `ENABLE_HARDENING=true` to activate sanitization and rate limiting. Without it, all injection payloads in seed data will execute unimpeded.

4. **The ATS sub-agent is ephemeral.** It's built fresh on each `trigger_ats_ranking` call — no persistent state between invocations. It reads from the evaluations table, so candidates must be evaluated before ranking.

5. **Context compression is lossy.** When the sliding window triggers, older tool call/result pairs are summarized into a single message. The LLM may lose fine-grained details from early conversation turns.

6. **Memory retrieval uses character n-grams, not real embeddings.** The cosine similarity search in `src/memory/retrieval.py` uses a simple char n-gram vectorizer — it's fast but not semantically deep. Good enough for keyword-level recall, poor for paraphrase matching.

7. **Redis is optional.** If `REDIS_URL` is not set, session storage (FastAPI) falls back to in-memory dicts and tool caching uses a local dict with TTL. Works for single-worker dev but breaks under multi-worker deployments.

8. **OpenRouter, not direct Anthropic/OpenAI.** The LLM calls go through OpenRouter using `langchain-openai`'s `ChatOpenAI` with a custom `base_url`. Model is DeepSeek V3.2, not Claude or GPT.

9. **Alembic migrations must run before first use.** `start.sh` handles this, but if you skip it, the app will crash on missing tables. Run `alembic upgrade head` manually if needed.

10. **Multi-tenant isolation is deliberately absent.** `client_id` is passed around but not enforced at the DB query level. A crafted prompt can access data across clients — this is by design for red-team testing.

---

## Tech Stack Summary

| Layer | Technology |
|-------|-----------|
| Agent Framework | LangGraph (ReAct pattern) |
| LLM | DeepSeek V3.2 via OpenRouter |
| Database | PostgreSQL (Neon) |
| Migrations | Alembic |
| UI | Streamlit |
| API | FastAPI + Uvicorn |
| Resume Parsing | pdfplumber + python-docx |
| Web Search | Tavily |
| Web Scraping | requests + BeautifulSoup4 |
| Caching | Redis (optional) + in-memory fallback |
| Guardrails | NeMo Guardrails (optional) |
| Observability | Langfuse / LangSmith / Galileo AI |
| Testing | pytest + pytest-asyncio |

---

## License

This project is for **educational and security research purposes only**.
