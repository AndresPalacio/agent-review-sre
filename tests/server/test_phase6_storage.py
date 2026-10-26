from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.server.storage import Base, CheckpointThreadRow, GatewayEventRow, Storage


async def _storage() -> tuple[Storage, object]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return Storage(engine), engine


async def test_schedule_round_trip_and_disabled_default() -> None:
    storage, engine = await _storage()
    definition = {"kind": "market_watch", "interval_minutes": 5, "rules": []}
    await storage.put_schedule("aapl-watch", definition)
    assert await storage.get_schedule("aapl-watch") == {
        "id": "aapl-watch",
        "definition": definition,
        "enabled": False,
    }
    await storage.put_schedule("aapl-watch", definition, enabled=True)
    assert [item["id"] for item in await storage.list_schedules()] == ["aapl-watch"]
    assert (await storage.get_schedule("aapl-watch"))["enabled"] is True
    assert await storage.delete_schedule("aapl-watch")
    assert await storage.get_schedule("aapl-watch") is None
    await engine.dispose()  # type: ignore[attr-defined]


async def test_schedule_create_is_atomic_and_preserves_existing_definition() -> None:
    storage, engine = await _storage()
    assert await storage.create_schedule("aapl-watch", {"kind": "portfolio_digest"}, enabled=False)
    assert not await storage.create_schedule("aapl-watch", {"kind": "market_watch"}, enabled=True)
    assert (await storage.get_schedule("aapl-watch"))["definition"] == {"kind": "portfolio_digest"}  # type: ignore[index]
    await engine.dispose()  # type: ignore[attr-defined]


async def test_watch_results_are_bounded_and_ordered() -> None:
    storage, engine = await _storage()
    now = datetime.now(UTC)
    await storage.record_watch_result("aapl", "normal", {"value": 1}, now - timedelta(hours=2))
    await storage.record_watch_result("aapl", "triggered", {"value": 9}, now)
    rows = await storage.recent_watch_results(now - timedelta(hours=1), limit=1)
    assert len(rows) == 1
    assert rows[0]["status"] == "triggered"
    assert rows[0]["details"] == {"value": 9}
    await engine.dispose()  # type: ignore[attr-defined]


async def test_gateway_event_claim_and_thread_link_are_durable() -> None:
    storage, engine = await _storage()
    assert await storage.claim_gateway_event("slack", "Ev1")
    assert not await storage.claim_gateway_event("slack", "Ev1")
    assert await storage.claim_gateway_event("telegram", "Ev1")
    await storage.bind_gateway_thread("slack", "C1", "123.45", "thread-1")
    assert await storage.gateway_thread("slack", "C1", "123.45") == "thread-1"
    assert await storage.gateway_thread("slack", "C2", "123.45") is None
    await engine.dispose()  # type: ignore[attr-defined]


async def test_approval_message_binding_survives_reopen_and_cannot_be_reassigned() -> None:
    storage, engine = await _storage()
    await storage.bind_gateway_thread(
        "slack", "C1", "123.45", "thread-1", approval_id="interrupt-1"
    )
    reopened = Storage(engine)  # type: ignore[arg-type]
    assert await reopened.gateway_approval_id("slack", "C1", "123.45") == "interrupt-1"
    with pytest.raises(ValueError, match="another request"):
        await reopened.bind_gateway_thread(
            "slack", "C1", "123.45", "thread-1", approval_id="interrupt-2"
        )
    await reopened.bind_gateway_thread("telegram", "chat", "1", "thread-1")
    assert await reopened.gateway_approval_id("telegram", "chat", "1") is None
    await engine.dispose()  # type: ignore[attr-defined]


async def test_gateway_inbox_retains_pending_payload_for_recovery() -> None:
    storage, engine = await _storage()
    payload = {"type": "analysis", "text": "analiza AAPL", "destination": "C1"}
    assert await storage.claim_gateway_event("slack", "Ev-recover", payload)
    assert not await storage.claim_gateway_event("slack", "Ev-recover", payload)
    old = datetime.now(UTC) - timedelta(hours=1)
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(update(GatewayEventRow).values(updated_at=old))
    assert await storage.pending_gateway_events("slack", datetime.now(UTC)) == [
        ("Ev-recover", payload)
    ]
    await storage.complete_gateway_event("slack", "Ev-recover")
    assert await storage.pending_gateway_events("slack", datetime.now(UTC)) == []
    await engine.dispose()  # type: ignore[attr-defined]


async def test_gateway_replay_claim_is_atomic_and_heartbeat_blocks_takeover() -> None:
    storage, engine = await _storage()
    second = Storage(engine)  # Separate session, as a second worker would use.
    payload = {"type": "analysis", "text": "analiza AAPL", "destination": "C1"}
    assert await storage.claim_gateway_event("slack", "Ev-race", payload)
    cutoff = datetime.now(UTC) - timedelta(minutes=2)
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(
            update(GatewayEventRow)
            .where(GatewayEventRow.event_id == "Ev-race")
            .values(updated_at=cutoff - timedelta(seconds=1))
        )
    assert await storage.claim_stale_gateway_event("slack", "Ev-race", cutoff)
    assert not await second.claim_stale_gateway_event("slack", "Ev-race", cutoff)
    assert await storage.touch_gateway_event("slack", "Ev-race")
    assert not await second.claim_stale_gateway_event("slack", "Ev-race", cutoff)
    await engine.dispose()  # type: ignore[attr-defined]


async def test_replay_takeover_revokes_original_gateway_worker() -> None:
    storage, engine = await _storage()
    assert await storage.claim_gateway_event("slack", "Ev-owner", {"type": "analysis"}, "original")
    cutoff = datetime.now(UTC) - timedelta(minutes=2)
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(
            update(GatewayEventRow)
            .where(GatewayEventRow.event_id == "Ev-owner")
            .values(updated_at=cutoff - timedelta(seconds=1))
        )
    assert await storage.claim_stale_gateway_event("slack", "Ev-owner", cutoff, "replacement")
    assert not await storage.touch_gateway_event("slack", "Ev-owner", "original")
    assert not await storage.complete_gateway_event("slack", "Ev-owner", "original")
    assert await storage.touch_gateway_event("slack", "Ev-owner", "replacement")
    assert await storage.complete_gateway_event("slack", "Ev-owner", "replacement")
    await engine.dispose()  # type: ignore[attr-defined]


async def test_gateway_run_recovery_claims_abandoned_queued_run() -> None:
    storage, engine = await _storage()
    await storage.create_run("orphan", "thread")
    cutoff = datetime.now(UTC) - timedelta(minutes=2)
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        from fin_graph.server.storage import RunRow

        await connection.execute(
            update(RunRow)
            .where(RunRow.run_id == "orphan")
            .values(updated_at=cutoff - timedelta(seconds=1))
        )
    assert await storage.claim_gateway_run_recovery("orphan", cutoff)
    assert not await Storage(engine).claim_gateway_run_recovery("orphan", cutoff)
    assert (await storage.get_run("orphan"))["status"] == "recovering"  # type: ignore[index]
    await engine.dispose()  # type: ignore[attr-defined]


async def test_gateway_permanent_failure_stops_replay_after_five_attempts() -> None:
    storage, engine = await _storage()
    payload = {"type": "analysis", "text": "analiza AAPL", "destination": "C1"}
    assert await storage.claim_gateway_event("telegram", "Ev-dead", payload)
    for attempt in range(5):
        assert await storage.fail_gateway_event("telegram", "Ev-dead") == (attempt < 4)
    assert await storage.pending_gateway_events("telegram", datetime.now(UTC)) == []
    await engine.dispose()  # type: ignore[attr-defined]


async def test_gateway_run_restart_is_compare_and_swap() -> None:
    storage, engine = await _storage()
    await storage.create_run("run-1", "gateway-thread")
    assert not await storage.restart_interrupted_run("run-1")
    await storage.set_status("run-1", "interrupted")
    assert await storage.restart_interrupted_run("run-1")
    assert not await storage.restart_interrupted_run("run-1")
    assert (await storage.get_run("run-1"))["status"] == "queued"  # type: ignore[index]
    await engine.dispose()  # type: ignore[attr-defined]


async def test_approval_candidates_only_include_old_chat_and_orders() -> None:
    storage, engine = await _storage()
    await storage.touch_checkpoint_thread("old", "chat")
    await storage.touch_checkpoint_thread("fresh", "orders")
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(
            update(CheckpointThreadRow)
            .where(CheckpointThreadRow.thread_id == "old")
            .values(touched_at=datetime.now(UTC) - timedelta(days=2))
        )
    assert await storage.list_approval_candidates(datetime.now(UTC) - timedelta(days=1)) == [
        ("chat", "old")
    ]
    await engine.dispose()  # type: ignore[attr-defined]


async def test_digest_delivery_claim_is_fail_closed_after_uncertain_send() -> None:
    storage, engine = await _storage()
    assert await storage.claim_delivery("digest:one", "C1") == "new"
    assert await storage.claim_delivery("digest:one", "C1") == "pending"
    await storage.complete_delivery("digest:one")
    assert await storage.claim_delivery("digest:one", "C1") == "completed"
    await engine.dispose()  # type: ignore[attr-defined]


async def test_retention_cleans_phase6_metadata_and_gateway_links() -> None:
    storage, engine = await _storage()
    old = datetime.now(UTC) - timedelta(days=40)
    cutoff = datetime.now(UTC) - timedelta(days=30)
    await storage.touch_checkpoint_thread("old-thread", "chat")
    await storage.bind_gateway_thread("slack", "C1", "1.2", "old-thread")
    await storage.claim_gateway_event("slack", "old-event")
    await storage.claim_gateway_event("slack", "new-event")
    await storage.record_watch_result("aapl", "normal", {}, old)
    await storage.record_watch_result("aapl", "triggered", {}, datetime.now(UTC))
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(update(CheckpointThreadRow).values(touched_at=old))
        await connection.execute(
            update(GatewayEventRow)
            .where(GatewayEventRow.event_id == "old-event")
            .values(created_at=old)
        )
    assert await storage.purge_before(cutoff) == ["old-thread"]
    assert await storage.gateway_thread("slack", "C1", "1.2") is None
    assert len(await storage.recent_watch_results(datetime.min.replace(tzinfo=UTC))) == 1
    assert await storage.claim_gateway_event("slack", "old-event")
    assert not await storage.claim_gateway_event("slack", "new-event")
    await engine.dispose()  # type: ignore[attr-defined]
