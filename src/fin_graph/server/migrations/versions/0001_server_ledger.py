"""Initial server ledger.

Revision ID: 0001_server_ledger
Revises:
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String

revision = "0001_server_ledger"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        Column("run_id", String(64), primary_key=True),
        Column("thread_id", String(64), nullable=False),
        Column("status", String(20), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        Column("error", String(120), nullable=True),
    )
    op.create_index("ix_runs_thread_id", "runs", ["thread_id"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_created_at", "runs", ["created_at"])
    op.create_table(
        "run_events",
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("run_id", String(64), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
        Column("kind", String(30), nullable=False),
        Column("data", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_table(
        "signal_fingerprints",
        Column("source", String(30), primary_key=True),
        Column("fingerprint", String(64), primary_key=True),
        Column("thread_id", String(64), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_signal_fingerprints_expires_at", "signal_fingerprints", ["expires_at"])
    op.create_table(
        "api_keys",
        Column("key_hash", String(64), primary_key=True),
        Column("role", String(20), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "deliveries",
        Column("idempotency_key", String(128), primary_key=True),
        Column("destination", String(64), nullable=False),
        Column("status", String(20), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "schedules",
        Column("id", String(64), primary_key=True),
        Column("definition", JSON, nullable=False),
        Column("enabled", Boolean, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("schedules")
    op.drop_table("deliveries")
    op.drop_table("api_keys")
    op.drop_index("ix_signal_fingerprints_expires_at", table_name="signal_fingerprints")
    op.drop_table("signal_fingerprints")
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("ix_runs_created_at", table_name="runs")
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_thread_id", table_name="runs")
    op.drop_table("runs")
