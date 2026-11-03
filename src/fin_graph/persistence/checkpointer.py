"""Checkpointer per backend: memory for tests, SQLite for the local CLI, Postgres for the server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from fin_graph.config.settings import PersistenceBackend, Settings
from fin_graph.persistence.paths import prepare_sqlite_path
from fin_graph.persistence.urls import psycopg_connection_url


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """Yield a ready checkpointer for the configured backend, creating its tables if missing."""
    backend = settings.persistence_backend
    if backend is PersistenceBackend.MEMORY:
        yield InMemorySaver()
    elif backend is PersistenceBackend.SQLITE:
        path = prepare_sqlite_path(settings.sqlite_path)
        async with AsyncSqliteSaver.from_conn_string(path) as sqlite_saver:
            await sqlite_saver.setup()
            yield sqlite_saver
    else:
        url = psycopg_connection_url(settings.require_database_url())
        async with AsyncPostgresSaver.from_conn_string(url) as postgres_saver:
            await postgres_saver.setup()
            yield postgres_saver
