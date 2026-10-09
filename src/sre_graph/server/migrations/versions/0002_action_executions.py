"""Durable fail-closed action claims.

Revision ID: 0002_action_executions
Revises: 0001_server_ledger
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import JSON, Column, DateTime, String

revision = "0002_action_executions"
down_revision = "0001_server_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_executions",
        Column("key", String(128), primary_key=True),
        Column("fingerprint", String(64), nullable=False),
        Column("status", String(20), nullable=False),
        Column("result", JSON, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("action_executions")
