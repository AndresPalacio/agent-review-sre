from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.server import migrate as migrate_module
from fin_graph.server.migrate import upgrade

LEGACY_TABLES = {"alert" + "_fingerprints", "health" + "_results"}


def _upgrade_to(database_url: str, revision: str) -> None:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(migrate_module.__file__).parent / "migrations")
    )
    config.attributes["database_url"] = database_url
    command.upgrade(config, revision)


async def test_financial_table_names_exist_before_compatibility_migration(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'pre-compat-migration.sqlite'}"
    await asyncio.to_thread(_upgrade_to, url, "0011_paper_ledger")
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))

    assert {"signal_fingerprints", "watch_results"} <= tables
    assert LEGACY_TABLES.isdisjoint(tables)
    await engine.dispose()


async def test_alembic_upgrade_is_repeatable(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'migration.sqlite'}"
    await asyncio.to_thread(upgrade, url)
    await asyncio.to_thread(upgrade, url)
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        action_columns = await connection.run_sync(
            lambda sync: {
                column["name"] for column in inspect(sync).get_columns("action_executions")
            }
        )
        gateway_columns = await connection.run_sync(
            lambda sync: {column["name"] for column in inspect(sync).get_columns("gateway_events")}
        )
        binding_columns = await connection.run_sync(
            lambda sync: {column["name"] for column in inspect(sync).get_columns("gateway_threads")}
        )
    assert {
        "runs",
        "run_events",
        "signal_fingerprints",
        "api_keys",
        "deliveries",
        "action_executions",
        "checkpoint_threads",
        "approval_decisions",
        "schedules",
        "watch_results",
        "paper_accounts",
        "paper_orders",
        "gateway_events",
        "gateway_threads",
        "compat_flows",
        "compat_flow_revisions",
        "compat_flow_runs",
        "compat_run_cycles",
        "compat_provider_keys",
        "compat_backtest_jobs",
        "compat_artifacts",
        "alembic_version",
    } <= tables
    assert LEGACY_TABLES.isdisjoint(tables)
    assert "thread_id" in action_columns
    assert {"status", "payload", "updated_at", "attempts", "owner"} <= gateway_columns
    assert "approval_id" in binding_columns
    await engine.dispose()
