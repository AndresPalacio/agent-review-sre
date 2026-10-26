from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.server.storage import Base, RunRow, Storage


async def test_action_claim_fails_closed_after_uncertain_outcome() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)

    fingerprint = "a" * 64
    assert await storage.claim_action("action-1", fingerprint) == ("new", None)
    assert await storage.claim_action("action-1", fingerprint) == ("pending", None)
    await storage.complete_action("action-1", {"replicas": 2})
    assert await storage.claim_action("action-1", fingerprint) == ("completed", {"replicas": 2})
    await engine.dispose()


async def test_retention_includes_chat_and_order_activity() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.touch_checkpoint_thread("chat-1", "chat")
    await storage.touch_checkpoint_thread("ord-1", "orders")
    await storage.create_run("run-1", "shared")
    await storage.touch_checkpoint_thread("shared", "chat")
    cutoff = datetime.now(UTC) + timedelta(days=1)
    assert set(await storage.expired_threads(cutoff)) == {"chat-1", "ord-1", "shared"}
    assert await storage.delete_thread("chat-1", cutoff)
    assert await storage.delete_thread("ord-1", cutoff)
    assert await storage.delete_thread("shared", cutoff)
    assert await storage.expired_threads(cutoff) == []
    await engine.dispose()


async def test_recent_chat_activity_keeps_older_analysis_thread() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.create_run("run-1", "shared")
    async with engine.begin() as connection:
        await connection.execute(
            update(RunRow)
            .where(RunRow.run_id == "run-1")
            .values(updated_at=datetime.now(UTC) - timedelta(days=40))
        )
    await storage.touch_checkpoint_thread("shared", "chat")
    cutoff = datetime.now(UTC) - timedelta(days=30)
    assert "shared" not in await storage.expired_threads(cutoff)
    assert not await storage.delete_thread("shared", cutoff)
    await engine.dispose()


async def test_approval_audit_records_actor_decision_without_raw_arguments() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.record_approval(
        "chat-1", "chat", "actor-hash", "approve", "propose_order", "a" * 64
    )
    records = await storage.approvals("chat-1")
    assert len(records) == 1
    assert records[0]["actor"] == "actor-hash"
    assert records[0]["decision"] == "approve"
    assert "arguments" not in str(records)
    await engine.dispose()


async def test_retention_deletes_only_expired_threads_action_results() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.touch_checkpoint_thread("old", "chat")
    await storage.touch_checkpoint_thread("new", "chat")
    assert await storage.claim_action("old-action", "a" * 64, thread_id="chat:old") == ("new", None)
    assert await storage.claim_action("legacy-action", "c" * 64, thread_id="remediation:old") == (
        "new",
        None,
    )
    assert await storage.claim_action("new-action", "b" * 64, thread_id="chat:new") == ("new", None)
    await storage.complete_action("old-action", {"ok": True})
    await storage.complete_action("legacy-action", {"ok": True})
    await storage.complete_action("new-action", {"ok": True})
    cutoff = datetime.now(UTC) + timedelta(days=1)
    assert await storage.delete_thread("old", cutoff)
    assert await storage.claim_action("old-action", "a" * 64, thread_id="chat:old") == ("new", None)
    assert await storage.claim_action("legacy-action", "c" * 64, thread_id="remediation:old") == (
        "new",
        None,
    )
    assert await storage.claim_action("new-action", "b" * 64, thread_id="chat:new") == (
        "completed",
        {"ok": True},
    )
    await engine.dispose()


async def test_run_event_dedupe_and_recovery() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.create_run("r1", "t1")
    await storage.set_status("r1", "running")
    await storage.add_event("r1", "stage", {"stage": "plan"})
    assert (await storage.events("t1", 0))[0]["data"] == {"stage": "plan"}
    cutoff = datetime.now(UTC) + timedelta(seconds=1)
    assert await storage.stale_running_candidates(cutoff) == [("r1", "t1")]
    assert await storage.interrupt_stale_run("r1", cutoff)
    assert (await storage.get_run("r1"))["status"] == "interrupted"
    now = datetime.now(UTC)
    assert await storage.claim_fingerprint("tradingview", "abc", "t1", now, timedelta(minutes=5))
    assert not await storage.claim_fingerprint(
        "tradingview", "abc", "t2", now, timedelta(minutes=5)
    )
    assert await storage.claim_fingerprint(
        "tradingview", "abc", "t2", now + timedelta(minutes=6), timedelta(minutes=5)
    )
    await engine.dispose()


async def test_api_key_roles_and_retention() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("secret", "operator")
    assert await storage.key_role("secret") == "operator"
    assert await storage.key_role("wrong") is None
    await storage.create_run("r1", "t1")
    assert await storage.purge_before(datetime.now(UTC) + timedelta(days=1)) == ["t1"]
    await storage.create_run("r2", "t2")
    assert await storage.purge_before(datetime.now(UTC) - timedelta(days=1)) == []
    assert (await storage.get_run("r2"))["thread_id"] == "t2"
    await engine.dispose()


async def test_retention_keeps_thread_with_newer_resume() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.create_run("old", "same")
    async with storage.sessions.begin() as session:
        await session.execute(
            update(RunRow)
            .where(RunRow.run_id == "old")
            .values(created_at=datetime.now(UTC) - timedelta(days=40))
        )
    await storage.create_run("new", "same")
    assert await storage.purge_before(datetime.now(UTC) - timedelta(days=30)) == []
    assert await storage.get_run("old") is not None
    assert await storage.get_run("new") is not None
    await engine.dispose()


async def test_webhook_claim_and_run_are_committed_together() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    now = datetime.now(UTC)
    assert await storage.claim_webhook_run(
        "tradingview", "f1", "t1", "r1", now, timedelta(minutes=5)
    ) == (True, "t1")
    assert (await storage.get_run("r1"))["thread_id"] == "t1"
    assert await storage.claim_webhook_run(
        "tradingview", "f1", "t2", "r2", now, timedelta(minutes=5)
    ) == (False, "t1")
    assert await storage.get_run("r2") is None
    await engine.dispose()


async def test_cancelled_query_does_not_leave_sqlite_locked(tmp_path: Path) -> None:
    # Cancellation used to cut a query off midway. The interrupted cursor stayed
    # referenced by the task's CancelledError traceback, and SQLite kept that
    # connection's read lock, so every later write failed with "database is locked".
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'cancel.sqlite'}", connect_args={"timeout": 1}
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.create_run("run", "thread")
    loop = asyncio.get_running_loop()
    armed = threading.Event()
    victim: asyncio.Task[object] | None = None

    def interrupt_in_worker() -> int:
        # Runs in the aiosqlite thread while the statement is executing.
        if armed.is_set() and victim is not None:
            armed.clear()
            loop.call_soon_threadsafe(victim.cancel)
            time.sleep(0.2)
        return 0

    @event.listens_for(engine.sync_engine, "connect")
    def install(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.run_async(  # type: ignore[attr-defined]
            lambda raw: raw.set_progress_handler(interrupt_in_worker, 1)
        )

    async def interrupted_run() -> None:
        # Same shape as RunManager._execute: record the interruption while the
        # CancelledError, and its traceback, is still being handled.
        try:
            await storage.get_run("run")
        except asyncio.CancelledError:
            await asyncio.wait_for(storage.set_status_with_event("run", "interrupted"), 10)
            raise

    await engine.dispose()  # reconnect so every pooled connection has the handler
    victim = asyncio.create_task(interrupted_run())
    armed.set()
    with pytest.raises(asyncio.CancelledError):
        await victim
    run = await storage.get_run("run")
    assert run is not None and run["status"] == "interrupted"
    await engine.dispose()
