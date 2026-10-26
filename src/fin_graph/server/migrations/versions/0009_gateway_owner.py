"""Fence stale gateway workers with an event owner.

Revision ID: 0009_gateway_owner
Revises: 0008_gateway_attempts
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, String

revision = "0009_gateway_owner"
down_revision = "0008_gateway_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gateway_events") as batch:
        batch.add_column(Column("owner", String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("gateway_events") as batch:
        batch.drop_column("owner")
