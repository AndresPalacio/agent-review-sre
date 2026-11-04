"""Add the durable financial compatibility ledger.

Revision ID: 0013_compatibility_store
Revises: 0011_paper_ledger
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0013_compatibility_store"
down_revision = "0011_paper_ledger"
branch_labels = None
depends_on = None


def _record_columns() -> tuple[Any, ...]:
    return (
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "compat_flows",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("active_revision", sa.Integer(), nullable=False),
        *_record_columns(),
    )
    op.create_table(
        "compat_flow_revisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "flow_id",
            sa.String(),
            sa.ForeignKey("compat_flows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        *_record_columns(),
        sa.UniqueConstraint("flow_id", "revision", name="uq_compat_flow_revision"),
    )
    op.create_table(
        "compat_flow_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "flow_id",
            sa.String(),
            sa.ForeignKey("compat_flows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("flow_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("result", sa.Text()),
        sa.Column("error", sa.Text()),
        *_record_columns(),
    )
    op.create_table(
        "compat_run_cycles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(),
            sa.ForeignKey("compat_flow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        *_record_columns(),
        sa.UniqueConstraint("run_id", "sequence", name="uq_compat_run_cycle_sequence"),
        sa.CheckConstraint("length(trim(event)) > 0", name="ck_compat_run_cycle_event"),
    )
    op.create_table(
        "compat_provider_keys",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False, unique=True),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(), nullable=False),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("description", sa.Text()),
        sa.Column("last_used", sa.String()),
        *_record_columns(),
    )
    op.create_table(
        "compat_backtest_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("result", sa.Text()),
        sa.Column("error", sa.Text()),
        *_record_columns(),
        sa.CheckConstraint("length(trim(kind)) > 0", name="ck_compat_backtest_job_kind"),
    )
    op.create_table(
        "compat_artifacts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(),
            sa.ForeignKey("compat_backtest_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        *_record_columns(),
        sa.CheckConstraint("length(trim(kind)) > 0", name="ck_compat_artifact_kind"),
        sa.CheckConstraint("length(trim(path)) > 0", name="ck_compat_artifact_path"),
    )


def downgrade() -> None:
    for table in (
        "compat_artifacts",
        "compat_backtest_jobs",
        "compat_provider_keys",
        "compat_run_cycles",
        "compat_flow_runs",
        "compat_flow_revisions",
        "compat_flows",
    ):
        op.drop_table(table)
