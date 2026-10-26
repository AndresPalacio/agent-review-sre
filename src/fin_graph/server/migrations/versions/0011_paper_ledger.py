"""Paper broker ledger: one portfolio per account and one row per approved order.

Revision ID: 0011_paper_ledger
Revises: 0010_gateway_approval
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import JSON, Column, DateTime, Integer, String

revision = "0011_paper_ledger"
down_revision = "0010_gateway_approval"
branch_labels = None
depends_on = None

_ORDER_INDEXES = ("account_id", "ticker", "status", "session_date")


def upgrade() -> None:
    op.create_table(
        "paper_accounts",
        Column("account_id", String(64), primary_key=True),
        Column("portfolio", JSON, nullable=False),
        Column("version", Integer, nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "paper_orders",
        Column("client_order_id", String(128), primary_key=True),
        Column("account_id", String(64), nullable=False),
        Column("ticker", String(16), nullable=False),
        Column("status", String(20), nullable=False),
        Column("session_date", String(10), nullable=False),
        Column("execution", JSON, nullable=False),
        Column("submitted_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    for column in _ORDER_INDEXES:
        op.create_index(f"ix_paper_orders_{column}", "paper_orders", [column])


def downgrade() -> None:
    for column in _ORDER_INDEXES:
        op.drop_index(f"ix_paper_orders_{column}", table_name="paper_orders")
    op.drop_table("paper_orders")
    op.drop_table("paper_accounts")
