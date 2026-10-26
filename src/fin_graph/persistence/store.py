"""Long-term store per backend, chosen by the same setting as the checkpointer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langchain.embeddings import init_embeddings
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.store.postgres.base import PostgresIndexConfig
from langgraph.store.sqlite.aio import AsyncSqliteStore

from fin_graph.config.settings import PersistenceBackend, Settings
from fin_graph.persistence.paths import prepare_sqlite_path


@asynccontextmanager
async def open_store(settings: Settings) -> AsyncIterator[BaseStore]:
    """Yield a ready store, with optional Postgres semantic indexing."""
    backend = settings.persistence_backend
    if backend is PersistenceBackend.MEMORY:
        yield InMemoryStore()
    elif backend is PersistenceBackend.SQLITE:
        path = prepare_sqlite_path(settings.sqlite_path)
        async with AsyncSqliteStore.from_conn_string(path) as sqlite_store:
            await sqlite_store.setup()
            yield sqlite_store
    else:
        url = settings.require_database_url()
        index: PostgresIndexConfig | None = None
        if settings.embeddings_provider:
            if settings.embeddings_provider != "openai":
                raise ValueError("EMBEDDINGS_PROVIDER currently supports openai")
            index = {
                "dims": 1536,
                "embed": init_embeddings("openai:text-embedding-3-small"),
                "fields": ["title", "service", "category", "summary"],
            }
        async with AsyncPostgresStore.from_conn_string(url, index=index) as postgres_store:
            await postgres_store.setup()
            yield postgres_store
