"""Durable metadata-only human approval audit.

Revision ID: 0004_approval_decisions
Revises: 0003_checkpoint_threads
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, DateTime, Integer, String

revision = "0004_approval_decisions"
down_revision = "0003_checkpoint_threads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_decisions",
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("thread_id", String(64), nullable=False),
        Column("kind", String(20), nullable=False),
        Column("actor", String(128), nullable=False),
        Column("decision", String(20), nullable=False),
        Column("action_name", String(100), nullable=False),
        Column("args_digest", String(64), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approval_decisions_thread_id", "approval_decisions", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_approval_decisions_thread_id", table_name="approval_decisions")
    op.drop_table("approval_decisions")
