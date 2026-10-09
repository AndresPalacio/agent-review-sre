from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from sre_graph.server.migrate import upgrade
from sre_graph.server.storage import Base, Storage


@pytest.mark.postgres
async def test_close_retries_failed_disposal_without_reopening_pool(
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(postgres_url)
    storage = Storage(engine, lock_pool_size=1)
    original = AsyncEngine.dispose
    attempts = 0

    async def dispose(selected: AsyncEngine, close: bool = True) -> None:
        nonlocal attempts
        if selected is storage._lock_engine:
            attempts += 1
            if attempts == 1:
                raise RuntimeError("fixture dispose failure")
        await original(selected, close)

    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    try:
        async with storage.run_lock(uuid4().hex):
            pass
        with pytest.raises(RuntimeError, match="fixture dispose"):
            await storage.close()
        with pytest.raises(RuntimeError, match="closed"):
            async with storage.run_lock(uuid4().hex):
                pass
        await storage.close()
        await storage.close()
        assert attempts == 2
    finally:
        await storage.close()
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.parametrize("kind", ["run", "approval", "try_run"])
async def test_advisory_locks_do_not_starve_single_connection_ledger(
    postgres_url: str, kind: str
) -> None:
    await asyncio.to_thread(upgrade, postgres_url)
    engine = create_async_engine(postgres_url, pool_size=1, max_overflow=0, pool_timeout=0.1)
    storage = Storage(engine)
    thread, run = uuid4().hex, uuid4().hex
    try:
        await storage.create_run(run, thread)
        context = (
            storage.run_lock(thread)
            if kind == "run"
            else (
                storage.approval_lock("chat", thread)
                if kind == "approval"
                else storage.try_run_lock(thread)
            )
        )
        async with context:
            assert (await storage.latest_run(thread))["run_id"] == run
    finally:
        await storage.close()
        await engine.dispose()


async def test_atomic_status_event_rolls_back_together() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    storage = Storage(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await storage.create_run("run", "thread")

    def fail_event(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO run_events"):
            raise RuntimeError("simulated event insert failure")

    event.listen(engine.sync_engine, "before_cursor_execute", fail_event)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            await storage.set_status_with_event("run", "completed")
        assert (await storage.latest_run("thread"))["status"] == "queued"
        assert await storage.events("thread", 0) == []
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail_event)
        await engine.dispose()


async def test_atomic_status_event_commits_once_with_metadata() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    storage = Storage(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await storage.create_run("run", "thread")
    try:
        await storage.set_status_with_event(
            "run", "failed", "fixture_failure", data={"error": "fixture_failure"}
        )
        assert (await storage.latest_run("thread"))["status"] == "failed"
        events = await storage.events("thread", 0)
        assert len(events) == 1
        assert events[0]["data"] == {"status": "failed", "error": "fixture_failure"}
    finally:
        await engine.dispose()


async def test_conditional_stale_interrupt_emits_event_only_when_claimed() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    storage = Storage(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await storage.create_run("run", "thread")
    try:
        before = datetime.now(UTC) + timedelta(seconds=1)
        assert await storage.interrupt_stale_run("run", before)
        assert not await storage.interrupt_stale_run("run", before)
        assert (await storage.latest_run("thread"))["status"] == "interrupted"
        events = await storage.events("thread", 0)
        assert len(events) == 1 and events[0]["data"]["status"] == "interrupted"
    finally:
        await storage.close()
        await engine.dispose()


@pytest.mark.postgres
async def test_nested_locks_share_owned_connection_and_children_do_not(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url, pool_size=1, max_overflow=0, pool_timeout=0.1)
    storage = Storage(engine, lock_pool_size=1, lock_pool_timeout_seconds=0.05)
    thread = uuid4().hex
    try:
        async with (
            storage.run_lock(thread),
            storage.approval_lock("chat", thread),
            storage.approval_lock("remediation", thread),
        ):
            async with engine.connect() as connection:
                assert await connection.scalar(text("SELECT 1")) == 1

            async def child():
                async with storage.try_run_lock(thread) as acquired:
                    return acquired

            with pytest.raises(PoolTimeoutError):
                await asyncio.create_task(child())
            # Child task did not treat inherited context as permission to share its
            # parent's transaction (which would falsely report lock acquisition).
        async with storage.try_run_lock(thread) as acquired:
            assert acquired
    finally:
        await storage.close()
        await engine.dispose()


@pytest.mark.postgres
async def test_bounded_lock_pool_and_cancellation_release_all_capacity(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url, pool_size=1, max_overflow=0, pool_timeout=0.1)
    storage = Storage(engine, lock_pool_size=1, lock_pool_timeout_seconds=0.05)
    thread = uuid4().hex
    entered = asyncio.Event()

    async def holder():
        async with storage.run_lock(thread):
            entered.set()
            await asyncio.Event().wait()

    pending = asyncio.create_task(holder())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(PoolTimeoutError):
            async with storage.run_lock(uuid4().hex):
                pytest.fail("bounded advisory pool overflowed")
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        async with storage.try_run_lock(thread) as acquired:
            assert acquired
        await storage.close()
        await storage.close()  # Idempotent and does not dispose the supplied ledger.
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
        with pytest.raises(RuntimeError, match="closed"):
            async with storage.run_lock(thread):
                pytest.fail("closed advisory storage reopened")
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await storage.close()
        await engine.dispose()


@pytest.mark.postgres
async def test_distinct_lock_users_make_progress_with_one_ledger_connection(
    postgres_url: str,
) -> None:
    await asyncio.to_thread(upgrade, postgres_url)
    engine = create_async_engine(postgres_url, pool_size=1, max_overflow=0, pool_timeout=0.5)
    storage = Storage(engine, lock_pool_size=2, lock_pool_timeout_seconds=1)
    pairs = [(uuid4().hex, uuid4().hex) for _ in range(6)]
    try:
        for thread, run in pairs:
            await storage.create_run(run, thread)

        async def worker(thread, run):
            async with storage.run_lock(thread):
                assert (await storage.latest_run(thread))["run_id"] == run
                await storage.set_status_with_event(run, "completed")
                await asyncio.sleep(0.01)

        await asyncio.wait_for(asyncio.gather(*(worker(*pair) for pair in pairs)), 3)
        runs = await asyncio.gather(*(storage.latest_run(thread) for thread, _ in pairs))
        assert all(run["status"] == "completed" for run in runs)
    finally:
        await storage.close()
        await engine.dispose()
