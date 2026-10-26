"""Bound gateway inbox retries.

Revision ID: 0008_gateway_attempts
Revises: 0007_gateway_inbox
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, Integer, text

revision = "0008_gateway_attempts"
down_revision = "0007_gateway_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gateway_events") as batch:
        batch.add_column(Column("attempts", Integer, nullable=True))
    op.execute(text("UPDATE gateway_events SET attempts = 0"))
    with op.batch_alter_table("gateway_events") as batch:
        batch.alter_column("attempts", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("gateway_events") as batch:
        batch.drop_column("attempts")
