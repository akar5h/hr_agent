"""Database schema creation and teardown."""

from src.database.db import get_db


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    industry TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS hiring_rubrics (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    criteria TEXT NOT NULL,
    scoring_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (position_id) REFERENCES positions(id),
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE,
    resume_hash TEXT,
    resume_text TEXT,
    linkedin_url TEXT,
    website_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    position_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    technical_score REAL,
    experience_score REAL,
    culture_score REAL,
    communication_score REAL,
    overall_score REAL,
    reasoning TEXT,
    recommendation TEXT,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (candidate_id) REFERENCES candidates(id),
    FOREIGN KEY (position_id) REFERENCES positions(id),
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    memory_value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

DROP_TABLES_SQL = """
DROP TABLE IF EXISTS agent_memory;
DROP TABLE IF EXISTS evaluations;
DROP TABLE IF EXISTS candidates;
DROP TABLE IF EXISTS hiring_rubrics;
DROP TABLE IF EXISTS positions;
DROP TABLE IF EXISTS clients;
"""


def init_db() -> None:
    """Create all database tables for Phase 1."""
    with get_db() as conn:
        conn.executescript(CREATE_TABLES_SQL)
    print("Database initialized successfully.")


def drop_all_tables() -> None:
    """Drop all database tables in reverse dependency order."""
    with get_db() as conn:
        conn.executescript(DROP_TABLES_SQL)

