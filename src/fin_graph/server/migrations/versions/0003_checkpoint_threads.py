"""Track chat and remediation checkpoints for retention.

Revision ID: 0003_checkpoint_threads
Revises: 0002_action_executions
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, DateTime, String

revision = "0003_checkpoint_threads"
down_revision = "0002_action_executions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checkpoint_threads",
        Column("thread_id", String(64), primary_key=True),
        Column("kind", String(20), primary_key=True),
        Column("touched_at", DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_checkpoint_threads_touched_at", "checkpoint_threads", ["touched_at"])


def downgrade() -> None:
    op.drop_index("ix_checkpoint_threads_touched_at", table_name="checkpoint_threads")
    op.drop_table("checkpoint_threads")
