from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.server.migrate import upgrade


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
        "alembic_version",
    } <= tables
    assert "thread_id" in action_columns
    assert {"status", "payload", "updated_at", "attempts", "owner"} <= gateway_columns
    assert "approval_id" in binding_columns
    await engine.dispose()
