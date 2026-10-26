"""Durable pending gateway events for recovery after process failure.

Revision ID: 0007_gateway_inbox
Revises: 0006_phase6
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import JSON, Column, DateTime, String, text

revision = "0007_gateway_inbox"
down_revision = "0006_phase6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gateway_events") as batch:
        batch.add_column(Column("updated_at", DateTime(timezone=True), nullable=True))
        batch.add_column(Column("status", String(20), nullable=True))
        batch.add_column(Column("payload", JSON, nullable=True))
    op.execute(text("UPDATE gateway_events SET updated_at = created_at, status = 'completed'"))
    with op.batch_alter_table("gateway_events") as batch:
        batch.alter_column("updated_at", nullable=False)
        batch.alter_column("status", nullable=False)
        batch.create_index("ix_gateway_events_updated_at", ["updated_at"])


def downgrade() -> None:
    with op.batch_alter_table("gateway_events") as batch:
        batch.drop_index("ix_gateway_events_updated_at")
        batch.drop_column("payload")
        batch.drop_column("status")
        batch.drop_column("updated_at")
