from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.server.test_manager import ScriptedGraph

from fin_graph.scheduler.service import PgAdvisoryLease, SchedulerService
from fin_graph.server.events import EventListener
from fin_graph.server.manager import RunManager
from fin_graph.server.migrate import upgrade
from fin_graph.server.storage import Storage


@pytest.mark.postgres
async def test_notification_cross_replica_replay_and_reconnect(postgres_url: str):
    await asyncio.to_thread(upgrade, postgres_url)
    engine = create_async_engine(postgres_url)
    storage = Storage(engine)
    listener = EventListener(postgres_url, retry_seconds=0.02)
    await listener.start()
    try:
        await asyncio.wait_for(listener.ready.wait(), 5)
        thread, run = uuid4().hex, uuid4().hex
        await storage.create_run(run, thread)
        async with listener.subscribe(thread) as signal, listener.subscribe(thread) as second:
            first = await storage.add_event(run, "stage", {"stage": "plan"})
            await asyncio.wait_for(signal.wait(), 5)
            await asyncio.wait_for(second.wait(), 5)
            assert [e["id"] for e in await storage.events(thread, 0)] == [first]
            signal.clear()
            assert listener.connection is not None
            old_connection = listener.connection
            async with engine.begin() as conn:
                await conn.execute(
                    text("SELECT pg_terminate_backend(:pid)"),
                    {"pid": old_connection.info.backend_pid},
                )
            for _ in range(200):
                if (
                    listener.connection is not None
                    and listener.connection is not old_connection
                    and listener.ready.is_set()
                ):
                    break
                await asyncio.sleep(0.02)
            final = await storage.add_event(run, "status", {"status": "completed"})
            await asyncio.wait_for(signal.wait(), 5)
            assert [e["id"] for e in await storage.events(thread, first)] == [final]
        assert listener.subscribers == {}
    finally:
        await listener.stop()
        await storage.close()
        await engine.dispose()
    assert listener.task is None and listener.connection is None


@pytest.mark.postgres
async def test_concurrent_resume_accepts_one_run(postgres_url: str):
    await asyncio.to_thread(upgrade, postgres_url)
    engines = [create_async_engine(postgres_url) for _ in range(2)]
    graph = ScriptedGraph()
    thread = uuid4().hex
    graph.checkpoints[thread] = {"started": True}
    managers = [RunManager(graph, Storage(e), lambda: None) for e in engines]
    await managers[0].storage.create_run(uuid4().hex, thread)
    initial = await managers[0].storage.latest_run(thread)
    await managers[0].storage.set_status(initial["run_id"], "interrupted")
    try:
        results = await asyncio.gather(
            *(m.resume(thread) for m in managers), return_exceptions=True
        )
        assert sum(isinstance(r, dict) for r in results) == 1
        assert sum(isinstance(r, ValueError) for r in results) == 1
    finally:
        for m in managers:
            await m.stop()
            await m.storage.close()
        for e in engines:
            await e.dispose()


@pytest.mark.postgres
async def test_scheduler_automatic_takeover(postgres_url: str):
    class Store:
        async def list_schedules(self):
            return []

        async def get_schedule(self, schedule_id):
            return None

    def make():
        return SchedulerService(
            Store(),
            {},
            scheduler=AsyncIOScheduler(jobstores={"default": MemoryJobStore()}),
            lease=PgAdvisoryLease(postgres_url),
            election_interval_seconds=0.02,
        )

    leader, standby = make(), make()
    try:
        assert await leader.start()
        assert not await standby.start()
        await leader.stop()
        for _ in range(200):
            if standby.is_leader:
                break
            await asyncio.sleep(0.02)
        assert standby.is_leader
    finally:
        await leader.stop()
        await standby.stop()


@pytest.mark.postgres
async def test_notifications_wait_for_commit_and_rollback_is_silent(postgres_url: str):
    engine = create_async_engine(postgres_url)
    listener = EventListener(postgres_url)
    await listener.start()
    try:
        await asyncio.wait_for(listener.ready.wait(), 5)
        thread = uuid4().hex
        async with listener.subscribe(thread) as signal, engine.connect() as conn:
            transaction = await conn.begin()
            await conn.execute(
                text("SELECT pg_notify(:channel,:thread)"),
                {"channel": "fin_graph_events", "thread": thread},
            )
            await asyncio.sleep(0.05)
            assert not signal.is_set()
            await transaction.rollback()
            await asyncio.sleep(0.05)
            assert not signal.is_set()
            transaction = await conn.begin()
            await conn.execute(
                text("SELECT pg_notify(:channel,:thread)"),
                {"channel": "fin_graph_events", "thread": thread},
            )
            assert not signal.is_set()
            await transaction.commit()
            await asyncio.wait_for(signal.wait(), 5)
    finally:
        await listener.stop()
        await engine.dispose()
