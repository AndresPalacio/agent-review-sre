"""Atomic SQLite action claims for the local CLI."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from sqlalchemy import Table
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.server.storage import ActionExecutionRow, ApprovalDecisionRow, Storage


@asynccontextmanager
async def open_sqlite_action_ledger(path: Path) -> AsyncIterator[Storage]:
    """Share an action table across CLI processes; uncertain writes stay pending."""
    path = await asyncio.to_thread(_prepare_path, path)
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(path)),
        connect_args={"timeout": 30},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: cast(Table, ActionExecutionRow.__table__).create(sync, checkfirst=True)
            )
            await connection.run_sync(
                lambda sync: cast(Table, ApprovalDecisionRow.__table__).create(
                    sync, checkfirst=True
                )
            )
        yield Storage(engine)
    finally:
        await engine.dispose()


def _prepare_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved
