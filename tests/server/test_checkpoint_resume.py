from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import create_async_engine

from sre_graph.config.settings import PersistenceBackend, Settings
from sre_graph.persistence.checkpointer import open_checkpointer
from sre_graph.server.manager import RunManager
from sre_graph.server.storage import Base, Storage


class State(TypedDict, total=False):
    raw_alert: str
    marker: str
    report: dict[str, str]


def _graph(saver: Any, gate: asyncio.Event) -> Any:
    async def first(state: State) -> dict[str, str]:
        return {"marker": "checkpointed"}

    async def second(state: State) -> dict[str, dict[str, str]]:
        await gate.wait()
        return {"report": {"marker": state["marker"]}}

    graph = StateGraph(State)
    graph.add_node("first", first)
    graph.add_node("second", second)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)
    return graph.compile(checkpointer=saver)


async def test_restart_resumes_persisted_langgraph_checkpoint(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runs.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.SQLITE,
        sqlite_path=str(tmp_path / "checkpoints.sqlite"),
    )
    gate = asyncio.Event()
    async with open_checkpointer(settings) as saver:
        manager = RunManager(_graph(saver, gate), storage, lambda: None)
        await manager.start()
        run = await manager.submit("alert", None)
        for _ in range(100):
            snapshot = await manager.graph.aget_state(manager._config(run["thread_id"]))
            if snapshot.next == ("second",):
                break
            await asyncio.sleep(0.01)
        assert snapshot.next == ("second",)
        await manager.stop()
    async with open_checkpointer(settings) as reopened:
        second = RunManager(_graph(reopened, gate), storage, lambda: None)
        await second.start()
        gate.set()
        resumed = await second.resume(run["thread_id"])
        await second.wait(resumed["run_id"])
        snapshot = await second.graph.aget_state(second._config(run["thread_id"]))
        assert snapshot.values["report"] == {"marker": "checkpointed"}
        assert (await storage.latest_run(run["thread_id"]))["status"] == "completed"
        await second.stop()
    await engine.dispose()
