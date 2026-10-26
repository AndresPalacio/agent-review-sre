"""Name the shared tables for what they hold in finance.

``health_results`` keeps market-watch checks and ``alert_fingerprints`` deduplicates trading
signals, scheduled triggers and gateway messages. Their indexes are recreated under the new
names, which is portable across SQLite and Postgres. Old order threads keep working: a
checkpoint kind is a string column, and ``remediation`` rows age out through retention.

Revision ID: 0012_finance_names
Revises: 0011_paper_ledger
"""

from __future__ import annotations

from alembic import op

revision = "0012_finance_names"
down_revision = "0011_paper_ledger"
branch_labels = None
depends_on = None

_RENAMES = (
    ("health_results", "watch_results", ("schedule_id", "checked_at")),
    ("alert_fingerprints", "signal_fingerprints", ("expires_at",)),
)


def _move(old: str, new: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.drop_index(f"ix_{old}_{column}", table_name=old)
    op.rename_table(old, new)
    for column in columns:
        op.create_index(f"ix_{new}_{column}", new, [column])


def upgrade() -> None:
    for old, new, columns in _RENAMES:
        _move(old, new, columns)


def downgrade() -> None:
    for old, new, columns in reversed(_RENAMES):
        _move(new, old, columns)
