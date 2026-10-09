"""Alembic online environment; URL is passed in memory by the server."""

from __future__ import annotations

import asyncio
import sys

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from sre_graph.server.storage import Base

target_metadata = Base.metadata


async def _online() -> None:
    url = context.config.attributes["database_url"]
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_migrate)
    finally:
        await engine.dispose()


def _migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if sys.platform == "win32":
    asyncio.run(_online(), loop_factory=asyncio.SelectorEventLoop)
else:
    asyncio.run(_online())
