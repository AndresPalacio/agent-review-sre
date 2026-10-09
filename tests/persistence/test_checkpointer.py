from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from sre_graph.config.settings import PersistenceBackend, Settings
from sre_graph.persistence.checkpointer import open_checkpointer


class CounterState(TypedDict, total=False):
    n: int


def _counter_graph() -> StateGraph[CounterState]:
    def increment(state: CounterState) -> dict[str, Any]:
        return {"n": state.get("n", 0) + 1}

    graph = StateGraph(CounterState)
    graph.add_node("increment", increment)
    graph.add_edge(START, "increment")
    graph.add_edge("increment", END)
    return graph


async def _write_then_reopen_and_read(settings: Settings) -> int:
    config = {"configurable": {"thread_id": f"t-{uuid.uuid4()}"}}
    async with open_checkpointer(settings) as saver:
        await _counter_graph().compile(checkpointer=saver).ainvoke({"n": 1}, config)
    async with open_checkpointer(settings) as saver:
        snapshot = await _counter_graph().compile(checkpointer=saver).aget_state(config)
    return int(snapshot.values["n"])


async def test_memory_backend_yields_in_memory_saver() -> None:
    async with open_checkpointer(Settings(_env_file=None)) as saver:
        assert isinstance(saver, InMemorySaver)


async def test_sqlite_thread_state_survives_reopening(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.SQLITE,
        sqlite_path=str(tmp_path / "state.sqlite"),
    )

    assert await _write_then_reopen_and_read(settings) == 2


@pytest.mark.postgres
async def test_postgres_thread_state_survives_reopening(postgres_url: str) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.POSTGRES,
        database_url=postgres_url,
    )

    assert await _write_then_reopen_and_read(settings) == 2


async def test_postgres_without_url_fails_before_connecting() -> None:
    settings = Settings(_env_file=None, persistence_backend=PersistenceBackend.POSTGRES)

    with pytest.raises(ValueError, match="DATABASE_URL"):
        async with open_checkpointer(settings):
            pass


async def test_sqlite_path_in_a_missing_folder_is_created(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.SQLITE,
        sqlite_path=str(tmp_path / "data" / "nested" / "state.sqlite"),
    )

    assert await _write_then_reopen_and_read(settings) == 2
