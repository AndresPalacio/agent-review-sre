from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import create_async_engine
from tests.server.test_manager import ScriptedGraph

from fin_graph.server.app import create_app
from fin_graph.server.manager import RunManager
from fin_graph.server.storage import Base, Storage


class PausedTerminalStorage(Storage):
    """Expose the old status/event transaction gap deterministically."""

    def __init__(self, engine):
        super().__init__(engine)
        self.release_status = asyncio.Event()

    async def set_status(self, run_id, status, error=None):
        await super().set_status(run_id, status, error)
        if status == "completed":
            await self.release_status.wait()


async def test_sse_observing_terminal_status_always_receives_terminal_event(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'atomic-terminal.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = PausedTerminalStorage(engine)
    await storage.put_key("viewer-key", "viewer")
    graph = ScriptedGraph()
    graph.release.set()
    manager = RunManager(graph, storage, lambda: object())
    app = create_app(manager=manager)
    try:
        async with app.router.lifespan_context(app):
            run = await manager.submit("alert", None)
            for _ in range(100):
                current = await storage.get_run(run["run_id"])
                if current["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            assert current["status"] == "completed"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/v1/threads/{run['thread_id']}/stream", headers={"x-api-key": "viewer-key"}
                )
            assert response.status_code == 200
            assert '"status": "completed"' in response.text
            storage.release_status.set()
            await manager.wait(run["run_id"])
    finally:
        storage.release_status.set()
        await manager.stop()
        await engine.dispose()


async def test_surface_lock_maps_release_idle_threads(tmp_path: Path):
    from weakref import WeakValueDictionary

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'locks.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = create_app(manager=RunManager(ScriptedGraph(), Storage(engine), lambda: object()))
    for mapping in (app.state.chat_locks, app.state.order_locks):
        assert isinstance(mapping, WeakValueDictionary)
        lock = asyncio.Lock()
        mapping["temporary-thread"] = lock
        assert "temporary-thread" in mapping
        del lock
        assert "temporary-thread" not in mapping
    await engine.dispose()


async def test_sse_drains_terminal_commit_between_empty_read_and_status_query(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'read-race.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class CommitBetweenReads(Storage):
        raced = False

        async def events(self, thread_id, after_id, limit=500):
            rows = await super().events(thread_id, after_id, limit)
            if not self.raced:
                self.raced = True
                await self.set_status_with_event("terminal-run", "completed")
            return rows

    storage = CommitBetweenReads(engine)
    await storage.put_key("viewer-key", "viewer")
    manager = RunManager(ScriptedGraph(), storage, lambda: object())
    app = create_app(manager=manager)
    try:
        async with app.router.lifespan_context(app):
            await storage.create_run("terminal-run", "terminal-thread")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/threads/terminal-thread/stream", headers={"x-api-key": "viewer-key"}
                )
            assert response.status_code == 200
            assert '"status": "completed"' in response.text
    finally:
        await manager.stop()
        await engine.dispose()


async def test_partial_production_startup_stops_manager_before_closing_storage(
    tmp_path: Path, monkeypatch
):
    from contextlib import asynccontextmanager

    import pytest
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    import fin_graph.server.app as server_module
    from fin_graph.integrations.registry import IntegrationRegistry
    from fin_graph.server.config import ServerConfig

    url = f"sqlite+aiosqlite:///{tmp_path / 'startup.sqlite'}"
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    managers = []

    class ObservedManager(RunManager):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            managers.append(self)

    class Listener:
        async def start(self):
            pass

        async def stop(self):
            pass

        def __init__(self, *_):
            pass

    @asynccontextmanager
    async def saver(_):
        yield InMemorySaver()

    @asynccontextmanager
    async def store(_):
        yield InMemoryStore()

    def invalid_gateway():
        raise ValueError("invalid sandbox gateway")

    monkeypatch.setattr(server_module, "create_async_engine", lambda *_, **__: engine)
    monkeypatch.setattr(server_module, "upgrade", lambda *_: None)
    monkeypatch.setattr(ServerConfig, "require_database_url", lambda _: url)
    monkeypatch.setattr(server_module, "EventListener", Listener)
    monkeypatch.setattr(server_module, "RunManager", ObservedManager)
    monkeypatch.setattr(server_module, "open_checkpointer", saver)
    monkeypatch.setattr(server_module, "open_store", store)
    monkeypatch.setattr(server_module, "SlackGatewaySettings", invalid_gateway)
    app = create_app(
        config=ServerConfig(
            _env_file=None,
            database_url="postgresql://localhost/offline",
            bootstrap_admin_key="x" * 32,
        ),
        registry=IntegrationRegistry([], env_file=None),
        chat_graph=object(),
        order_graph=object(),
    )
    try:
        with pytest.raises(ValueError, match="invalid sandbox gateway"):
            async with app.router.lifespan_context(app):
                pass
        assert managers[0]._reconcile_task is None
    finally:
        for manager in managers:
            await manager.stop()
        await engine.dispose()


async def test_lock_acquisition_failure_becomes_durable_failed_event(tmp_path: Path):
    from contextlib import asynccontextmanager

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lock-failure.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class LockFailureStorage(Storage):
        @asynccontextmanager
        async def run_lock(self, thread_id):
            raise TimeoutError("lock pool busy")
            yield

    storage = LockFailureStorage(engine)
    manager = RunManager(ScriptedGraph(), storage, lambda: object())
    try:
        run = await manager.submit("alert", None)
        await manager.wait(run["run_id"])
        assert (await storage.get_run(run["run_id"]))["status"] == "failed"
        assert (await storage.events(run["thread_id"], 0))[-1]["data"]["status"] == "failed"
    finally:
        await manager.stop()
        await storage.close()
        await engine.dispose()


async def test_stale_unfinished_checkpoint_commits_interrupted_event(tmp_path: Path):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from fin_graph.server.storage import RunRow

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stale.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    graph = ScriptedGraph()
    graph.checkpoints["stale-thread"] = {"started": True}
    manager = RunManager(graph, storage, lambda: object())
    try:
        await storage.create_run("stale-run", "stale-thread")
        await storage.set_status("stale-run", "running")
        async with engine.begin() as connection:
            await connection.execute(
                update(RunRow)
                .where(RunRow.run_id == "stale-run")
                .values(updated_at=datetime.now(UTC) - timedelta(minutes=3))
            )
        await manager.reconcile_stale_runs()
        assert (await storage.get_run("stale-run"))["status"] == "interrupted"
        assert any(
            event["data"].get("status") == "interrupted"
            for event in await storage.events("stale-thread", 0)
        )
    finally:
        await manager.stop()
        await storage.close()
        await engine.dispose()
