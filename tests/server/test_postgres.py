from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.scheduler.service import PgAdvisoryLease, SchedulerService
from fin_graph.server.app import create_app
from fin_graph.server.config import ServerConfig
from fin_graph.server.migrate import upgrade
from fin_graph.server.storage import GatewayEventRow, Storage


@pytest.mark.postgres
async def test_server_lifespan_migrates_postgres_and_bootstraps_auth(postgres_url: str) -> None:
    key = uuid4().hex + uuid4().hex
    config = ServerConfig(
        _env_file=None, database_url=SecretStr(postgres_url), bootstrap_admin_key=SecretStr(key)
    )
    app = create_app(config=config, registry=IntegrationRegistry([], env_file=None))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/v1/ready")).status_code == 200
        assert app.state.scheduler.is_leader
        assert app.state.scheduler.scheduler.get_job("internal:retention") is not None
        assert app.state.scheduler.scheduler.get_job("internal:expiry") is not None
        assert app.state.scheduler.scheduler.get_job("internal:gateway_replay") is not None
        assert (await client.get("/v1/integrations", headers={"x-api-key": key})).json() == []
        storage = app.state.manager.storage
        fingerprint = uuid4().hex
        thread = uuid4().hex
        run_id = uuid4().hex
        assert await storage.claim_webhook_run(
            "tradingview",
            fingerprint,
            thread,
            run_id,
            datetime.now(UTC),
            timedelta(minutes=5),
        ) == (True, thread)
        assert await storage.claim_webhook_run(
            "tradingview",
            fingerprint,
            uuid4().hex,
            uuid4().hex,
            datetime.now(UTC),
            timedelta(minutes=5),
        ) == (False, thread)
        chat_thread = uuid4().hex
        await storage.touch_checkpoint_thread(chat_thread, "chat")
        await storage.record_approval(
            chat_thread, "chat", "actor-hash", "reject", "save_note", "a" * 64
        )
        assert chat_thread in await storage.expired_threads(datetime.now(UTC) + timedelta(days=1))
        assert (await storage.approvals(chat_thread))[0]["decision"] == "reject"
        assert await storage.delete_thread(chat_thread, datetime.now(UTC) + timedelta(days=1))


@pytest.mark.postgres
async def test_approval_lock_serializes_distinct_process_connections(postgres_url: str) -> None:
    import asyncio

    first_engine = create_async_engine(postgres_url)
    second_engine = create_async_engine(postgres_url)
    first, second = Storage(first_engine), Storage(second_engine)
    entered = asyncio.Event()

    async def contender() -> None:
        async with second.approval_lock("chat", "same-thread"):
            entered.set()

    try:
        async with first.approval_lock("chat", "same-thread"):
            task = asyncio.create_task(contender())
            await asyncio.sleep(0.1)
            assert not entered.is_set()
        await asyncio.wait_for(task, timeout=2)
        assert entered.is_set()
    finally:
        await first.close()
        await second.close()
        await first_engine.dispose()
        await second_engine.dispose()


@pytest.mark.postgres
async def test_scheduler_advisory_lock_allows_only_one_leader(postgres_url: str) -> None:
    class EmptySchedules:
        async def list_schedules(self) -> list[dict[str, object]]:
            return []

        async def get_schedule(self, schedule_id: str) -> None:
            return None

    def service() -> SchedulerService:
        return SchedulerService(
            EmptySchedules(),
            {},
            scheduler=AsyncIOScheduler(jobstores={"default": MemoryJobStore()}),
            lease=PgAdvisoryLease(postgres_url),
        )

    leader, standby = service(), service()
    try:
        assert await leader.start()
        assert not await standby.start()
        assert leader.is_leader and not standby.is_leader
        await leader.stop()
        assert await standby.start()
        assert standby.is_leader
    finally:
        await standby.stop()
        await leader.stop()


@pytest.mark.postgres
async def test_gateway_replay_and_run_claim_are_atomic_across_connections(
    postgres_url: str,
) -> None:
    await asyncio.to_thread(upgrade, postgres_url)
    first_engine = create_async_engine(postgres_url)
    second_engine = create_async_engine(postgres_url)
    first, second = Storage(first_engine), Storage(second_engine)
    event_id = uuid4().hex
    thread_id = uuid4().hex
    fingerprint = uuid4().hex
    cutoff = datetime.now(UTC) - timedelta(minutes=2)
    try:
        assert await first.claim_gateway_event("slack", event_id, {"type": "analysis"})
        async with first_engine.begin() as connection:
            await connection.execute(
                update(GatewayEventRow)
                .where(GatewayEventRow.event_id == event_id)
                .values(updated_at=cutoff - timedelta(seconds=1))
            )
        claims = await asyncio.gather(
            first.claim_stale_gateway_event("slack", event_id, cutoff),
            second.claim_stale_gateway_event("slack", event_id, cutoff),
        )
        assert sorted(claims) == [False, True]
        runs = await asyncio.gather(
            first.claim_webhook_run(
                "gateway",
                fingerprint,
                thread_id,
                uuid4().hex,
                datetime.now(UTC),
                timedelta(days=365),
            ),
            second.claim_webhook_run(
                "gateway",
                fingerprint,
                thread_id,
                uuid4().hex,
                datetime.now(UTC),
                timedelta(days=365),
            ),
        )
        assert sorted(claimed for claimed, _ in runs) == [False, True]
    finally:
        await first.close()
        await second.close()
        await first_engine.dispose()
        await second_engine.dispose()


@pytest.mark.postgres
async def test_run_lock_serializes_workers_across_connections(postgres_url: str) -> None:
    await asyncio.to_thread(upgrade, postgres_url)
    first_engine = create_async_engine(postgres_url)
    second_engine = create_async_engine(postgres_url)
    first, second = Storage(first_engine), Storage(second_engine)
    thread_id = uuid4().hex
    entered = asyncio.Event()

    async def contender() -> None:
        async with second.run_lock(thread_id):
            entered.set()

    try:
        async with first.run_lock(thread_id):
            async with second.try_run_lock(thread_id) as acquired:
                assert not acquired
            task = asyncio.create_task(contender())
            await asyncio.sleep(0.1)
            assert not entered.is_set()
        await asyncio.wait_for(task, timeout=2)
        assert entered.is_set()
        async with second.try_run_lock(thread_id) as acquired:
            assert acquired
    finally:
        await first.close()
        await second.close()
        await first_engine.dispose()
        await second_engine.dispose()
