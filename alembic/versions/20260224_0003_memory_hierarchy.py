"""Add memory hierarchy columns and indexes to agent_memory.

Revision ID: 20260224_0003
Revises: 20260224_0002
Create Date: 2026-02-24 08:30:00
"""

from __future__ import annotations

from alembic import op


revision = "20260224_0003"
down_revision = "20260224_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS memory_type TEXT DEFAULT 'episodic'")
    op.execute("ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
    op.execute("ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS embedding TEXT")
    op.execute("ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS access_count INTEGER DEFAULT 0")
    op.execute(
        "ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP"
    )
    op.execute("UPDATE agent_memory SET memory_type = 'episodic' WHERE memory_type IS NULL")
    op.execute("UPDATE agent_memory SET access_count = 0 WHERE access_count IS NULL")
    op.execute("UPDATE agent_memory SET updated_at = created_at WHERE updated_at IS NULL")
    op.execute("ALTER TABLE agent_memory ALTER COLUMN memory_type SET NOT NULL")
    op.execute("ALTER TABLE agent_memory ALTER COLUMN access_count SET NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_memory_memory_type ON agent_memory (client_id, memory_type)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_memory_expires_at ON agent_memory (expires_at)")


def downgrade() -> None:
    # Keep downgrade non-destructive for shared environments.
    pass
