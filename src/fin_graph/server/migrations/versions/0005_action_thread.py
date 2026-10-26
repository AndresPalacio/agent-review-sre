"""Associate action results with their checkpoint thread for safe retention.

Revision ID: 0005_action_thread
Revises: 0004_approval_decisions
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, String

revision = "0005_action_thread"
down_revision = "0004_approval_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("action_executions") as batch:
        batch.add_column(Column("thread_id", String(128), nullable=True))
        batch.create_index("ix_action_executions_thread_id", ["thread_id"])


def downgrade() -> None:
    with op.batch_alter_table("action_executions") as batch:
        batch.drop_index("ix_action_executions_thread_id")
        batch.drop_column("thread_id")
