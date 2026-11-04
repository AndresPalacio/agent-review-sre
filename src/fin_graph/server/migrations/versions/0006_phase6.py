"""Market watch results and gateway correlation.

Revision ID: 0006_phase6
Revises: 0005_action_thread
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import JSON, Column, DateTime, Integer, String

revision = "0006_phase6"
down_revision = "0005_action_thread"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watch_results",
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("schedule_id", String(64), nullable=False),
        Column("status", String(20), nullable=False),
        Column("details", JSON, nullable=False),
        Column("checked_at", DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_watch_results_schedule_id", "watch_results", ["schedule_id"])
    op.create_index("ix_watch_results_checked_at", "watch_results", ["checked_at"])
    op.create_table(
        "gateway_events",
        Column("platform", String(20), primary_key=True),
        Column("event_id", String(128), primary_key=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_gateway_events_created_at", "gateway_events", ["created_at"])
    op.create_table(
        "gateway_threads",
        Column("platform", String(20), primary_key=True),
        Column("destination", String(128), primary_key=True),
        Column("remote_id", String(128), primary_key=True),
        Column("thread_id", String(64), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_gateway_threads_thread_id", "gateway_threads", ["thread_id"])


def downgrade() -> None:
    op.drop_table("gateway_threads")
    op.drop_table("gateway_events")
    op.drop_table("watch_results")
