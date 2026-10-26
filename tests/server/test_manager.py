from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.server.manager import RunManager
from fin_graph.server.storage import Base, RunRow, Storage


class ScriptedGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str]] = []
        self.release = asyncio.Event()
        self.checkpoints: dict[str, dict[str, Any]] = {}

    async def astream(
        self, value: object, *, context: object, stream_mode: object, config: dict[str, Any]
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        thread = config["configurable"]["thread_id"]
        self.calls.append((value, thread))
        self.checkpoints[thread] = {"started": True}
        yield "custom", {"type": "stage", "data": {"stage": "plan"}}
        await self.release.wait()
        self.checkpoints[thread] = {"report": {"id": thread}}
        yield "values", self.checkpoints[thread]

    async def aget_state(self, config: dict[str, Any]) -> object:
        thread = config["configurable"]["thread_id"]
        return type(
            "Snapshot", (), {"values": self.checkpoints.get(thread, {}), "next": ("report",)}
        )()


async def test_manager_persists_only_safe_plan_and_specialist_metadata(tmp_path: Path) -> None:
    class ProgressGraph:
        async def astream(
            self, value: object, *, context: object, stream_mode: object, config: dict[str, Any]
        ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
            yield (
                "custom",
                {
                    "type": "plan",
                    "data": {
                        "stage": "plan",
                        "wave": 1,
                        "tasks": [
                            {
                                "domain": "metrics",
                                "hypothesis_ids": ["H1"],
                                "objective": "private agent prompt",
                            }
                        ],
                    },
                },
            )
            yield (
                "custom",
                {
                    "type": "specialist",
                    "data": {
                        "stage": "metrics",
                        "wave": 1,
                        "domain": "metrics",
                        "evidence_ids": ["ev_metrics"],
                        "args": {"password": "private"},
                    },
                },
            )
            yield "values", {"report": {"done": True}}

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'progress.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    manager = RunManager(ProgressGraph(), storage, lambda: object())
    run = await manager.submit("alert", None)
    await manager.wait(run["run_id"])
    events = await storage.events(run["thread_id"], 0)
    assert [event["type"] for event in events] == ["status", "plan", "specialist", "status"]
    assert events[1]["data"] == {
        "stage": "plan",
        "wave": 1,
        "tasks": [{"domain": "metrics", "hypothesis_ids": ["H1"]}],
    }
    assert events[2]["data"] == {
        "stage": "metrics",
        "wave": 1,
        "domain": "metrics",
        "evidence_ids": ["ev_metrics"],
    }
    await engine.dispose()


async def test_manager_stream_and_resume_after_restart(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'server.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    graph = ScriptedGraph()
    first = RunManager(graph, storage, lambda: object(), max_concurrent=1)
    await first.start()
    run = await first.submit("alert", None)
    for _ in range(100):
        if any(
            event["data"] == {"stage": "plan"}
            for event in await storage.events(run["thread_id"], 0)
        ):
            break
        await asyncio.sleep(0.01)
    assert (await storage.get_run(run["run_id"]))["status"] == "running"
    assert any(
        event["data"] == {"stage": "plan"} for event in await storage.events(run["thread_id"], 0)
    )
    await first.stop()
    assert (await storage.get_run(run["run_id"]))["status"] == "interrupted"
    second = RunManager(graph, storage, lambda: object(), max_concurrent=1)
    await second.start()
    graph.release.set()
    resumed = await second.resume(run["thread_id"])
    await second.wait(resumed["run_id"])
    assert graph.calls[-1] == (None, run["thread_id"])
    assert (await storage.latest_run(run["thread_id"]))["status"] == "completed"
    await second.stop()
    await engine.dispose()


async def test_gateway_recovery_resumes_partial_checkpoint_in_same_run(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway-recover.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    graph = ScriptedGraph()
    first = RunManager(graph, storage, lambda: object())
    await first.start()
    await storage.create_run("gateway-run", "gateway-thread")
    first.schedule_existing("gateway-run", "gateway-thread", "alert", None)
    for _ in range(100):
        if graph.calls:
            break
        await asyncio.sleep(0.01)
    await first.stop()
    assert (await storage.get_run("gateway-run"))["status"] == "interrupted"
    second = RunManager(graph, storage, lambda: object())
    await second.start()
    graph.release.set()
    assert await second.recover_existing("gateway-run", "gateway-thread", "alert")
    await second.wait("gateway-run")
    assert graph.calls[-1] == (None, "gateway-thread")
    assert (await storage.get_run("gateway-run"))["status"] == "completed"
    await second.stop()
    await engine.dispose()


async def test_gateway_recovery_restarts_orphaned_queued_run(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway-orphan.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    graph = ScriptedGraph()
    graph.release.set()
    await storage.create_run("orphan-run", "orphan-thread")
    async with engine.begin() as connection:
        await connection.execute(
            update(RunRow)
            .where(RunRow.run_id == "orphan-run")
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=3))
        )
    manager = RunManager(graph, storage, lambda: object())
    assert await manager.recover_existing("orphan-run", "orphan-thread", "alert")
    assert not await manager.recover_existing("orphan-run", "orphan-thread", "alert")
    await manager.wait("orphan-run")
    assert len(graph.calls) == 1
    assert graph.calls[0][1] == "orphan-thread"
    assert graph.calls[0][0]["raw_alert"] == "alert"  # type: ignore[index]
    assert (await storage.get_run("orphan-run"))["status"] == "completed"  # type: ignore[index]
    await engine.dispose()


async def test_start_reconciles_report_finished_before_status_write(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recover.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.create_run("r1", "t1")
    await storage.set_status("r1", "running")
    async with engine.begin() as connection:
        await connection.execute(
            update(RunRow)
            .where(RunRow.run_id == "r1")
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=3))
        )

    class FinishedGraph:
        async def aget_state(self, config: dict[str, Any]) -> object:
            return type("Snapshot", (), {"values": {"report": {"done": True}}, "next": ()})()

    manager = RunManager(FinishedGraph(), storage, lambda: None)
    await manager.start()
    assert (await storage.get_run("r1"))["status"] == "completed"
    await manager.stop()
    await engine.dispose()


async def test_start_releases_webhook_claim_without_checkpoint(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'orphan.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.claim_webhook_run(
        "alertmanager", "f1", "t1", "r1", datetime.now(UTC), timedelta(minutes=5)
    )
    async with engine.begin() as connection:
        await connection.execute(
            update(RunRow)
            .where(RunRow.run_id == "r1")
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=3))
        )

    class MissingGraph:
        async def aget_state(self, config: dict[str, Any]) -> object:
            return type("Snapshot", (), {"values": {}, "next": ()})()

    manager = RunManager(MissingGraph(), storage, lambda: None)
    await manager.start()
    assert (await storage.get_run("r1"))["status"] == "failed"
    assert await storage.fingerprint_thread("alertmanager", "f1") is None
    await manager.stop()
    await engine.dispose()


async def test_start_preserves_active_run_with_fresh_heartbeat(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'active.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.create_run("active", "active-thread")
    await storage.set_status("active", "running")

    class UnreadGraph:
        async def aget_state(self, config: dict[str, Any]) -> object:
            raise AssertionError("active run must not be reconciled")

    manager = RunManager(UnreadGraph(), storage, lambda: None)
    await manager.start()
    assert (await storage.get_run("active"))["status"] == "running"  # type: ignore[index]
    await manager.stop()
    await engine.dispose()


async def test_periodic_reconciliation_recovers_run_after_quick_restart(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'periodic.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.create_run("orphan", "thread")
    await storage.set_status("orphan", "running")

    class FinishedGraph:
        async def aget_state(self, config: dict[str, Any]) -> object:
            return type("Snapshot", (), {"values": {"report": {"done": True}}, "next": ()})()

    manager = RunManager(FinishedGraph(), storage, lambda: None, reconcile_interval_seconds=0.02)
    await manager.start()
    assert (await storage.get_run("orphan"))["status"] == "running"  # type: ignore[index]
    async with engine.begin() as connection:
        await connection.execute(
            update(RunRow)
            .where(RunRow.run_id == "orphan")
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=3))
        )
    for _ in range(100):
        if (await storage.get_run("orphan"))["status"] == "completed":  # type: ignore[index]
            break
        await asyncio.sleep(0.02)
    assert (await storage.get_run("orphan"))["status"] == "completed"  # type: ignore[index]
    await manager.stop()
    await engine.dispose()
