"""Bind remote approval messages to the exact LangGraph interrupt.

Revision ID: 0010_gateway_approval
Revises: 0009_gateway_owner
"""

from alembic import op
from sqlalchemy import Column, String

revision = "0010_gateway_approval"
down_revision = "0009_gateway_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gateway_threads") as batch:
        batch.add_column(Column("approval_id", String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("gateway_threads") as batch:
        batch.drop_column("approval_id")
