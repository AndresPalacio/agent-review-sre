from __future__ import annotations

import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from fin_graph.config.settings import PersistenceBackend, Settings
from fin_graph.persistence.checkpointer import open_checkpointer
from fin_graph.persistence.store import open_store
from fin_graph.persistence.urls import psycopg_connection_url


async def _put_then_reopen_and_get(settings: Settings) -> object:
    key = f"note-{uuid.uuid4()}"
    async with open_store(settings) as store:
        await store.aput(("org", "notes"), key, {"text": "rollback fixed it"})
    async with open_store(settings) as store:
        item = await store.aget(("org", "notes"), key)
    return None if item is None else item.value


async def test_sqlite_store_survives_reopening(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.SQLITE,
        sqlite_path=str(tmp_path / "state.sqlite"),
    )

    assert await _put_then_reopen_and_get(settings) == {"text": "rollback fixed it"}


def test_langgraph_postgres_url_removes_only_the_sqlalchemy_driver() -> None:
    assert (
        psycopg_connection_url("postgresql+psycopg://user:pass@db:5432/name?sslmode=require")
        == "postgresql://user:pass@db:5432/name?sslmode=require"
    )
    assert psycopg_connection_url("postgresql://user:pass@db/name") == (
        "postgresql://user:pass@db/name"
    )


async def test_postgres_embeddings_are_opt_in_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langgraph.store.postgres.aio import AsyncPostgresStore

    from fin_graph.persistence import store as store_module

    seen: dict[str, Any] = {}

    class FakeStore:
        async def setup(self) -> None:
            seen["setup"] = True

    @asynccontextmanager
    async def fake_connection(url: str, *, index: dict[str, Any] | None = None) -> Any:
        seen["url"] = url
        seen["index"] = index
        yield FakeStore()

    monkeypatch.setattr(AsyncPostgresStore, "from_conn_string", fake_connection)
    monkeypatch.setattr(
        store_module, "init_embeddings", lambda model: f"fake:{model}", raising=False
    )
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.POSTGRES,
        database_url="postgresql://offline/test",
        embeddings_provider="openai",
    )
    async with open_store(settings):
        pass
    assert seen["index"] == {
        "dims": 1536,
        "embed": "fake:openai:text-embedding-3-small",
        "fields": ["title", "service", "category", "summary"],
    }
    assert seen["setup"] is True


async def test_store_and_checkpointer_share_one_sqlite_file(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.SQLITE,
        sqlite_path=str(tmp_path / "state.sqlite"),
    )

    async with open_checkpointer(settings), open_store(settings) as store:
        await store.aput(("org", "notes"), "n1", {"text": "shared file"})
        item = await store.aget(("org", "notes"), "n1")
    with sqlite3.connect(tmp_path / "state.sqlite") as database:
        tables = {row[0] for row in database.execute("select name from sqlite_master")}

    assert item is not None
    assert item.value == {"text": "shared file"}
    assert {"checkpoints", "writes", "store"} <= tables


@pytest.mark.postgres
async def test_postgres_store_survives_reopening(postgres_url: str) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.POSTGRES,
        database_url=postgres_url,
    )

    assert await _put_then_reopen_and_get(settings) == {"text": "rollback fixed it"}
