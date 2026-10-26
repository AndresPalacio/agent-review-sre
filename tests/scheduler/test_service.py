from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from fin_graph.scheduler.service import SchedulerService, validate_schedule


class FakeLease:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.released = False
        self.healthy = True

    def acquire(self) -> bool:
        return self.available

    def release(self) -> None:
        self.released = True

    def held(self) -> bool:
        return self.healthy


class FakeStore:
    def __init__(self) -> None:
        self.schedules: dict[str, dict[str, Any]] = {}

    async def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        return self.schedules.get(schedule_id)

    async def list_schedules(self) -> list[dict[str, Any]]:
        return list(self.schedules.values())


def test_schedule_definition_rejects_invalid_or_unbounded_inputs() -> None:
    with pytest.raises(ValueError):
        validate_schedule({"kind": "health_check", "interval_minutes": 0, "checks": []})
    with pytest.raises(ValueError):
        validate_schedule({"kind": "digest", "hour": 25, "minute": 0, "timezone": "UTC"})
    with pytest.raises(ValueError):
        validate_schedule({"kind": "digest", "hour": 8, "minute": 0, "timezone": "Fake/Zone"})
    assert (
        validate_schedule(
            {
                "kind": "health_check",
                "interval_minutes": 5,
                "checks": [{"id": "cpu", "expression": "up", "operator": "lt", "threshold": 1}],
            }
        )["kind"]
        == "health_check"
    )


def test_schedule_rejects_blank_destinations() -> None:
    with pytest.raises(ValueError):
        validate_schedule(
            {
                "kind": "digest",
                "hour": 8,
                "minute": 0,
                "timezone": "UTC",
                "destinations": {"slack": "  "},
            }
        )


def test_schedule_normalizes_destination_whitespace() -> None:
    definition = validate_schedule(
        {
            "kind": "digest",
            "hour": 8,
            "minute": 0,
            "timezone": "UTC",
            "destinations": {"slack": " C1 ", "telegram": " 42 "},
        }
    )
    assert definition["destinations"] == {"slack": "C1", "telegram": "42"}


async def test_nonleader_does_not_start_jobs() -> None:
    store = FakeStore()
    calls: list[str] = []

    async def handler(schedule_id: str, definition: dict[str, Any]) -> None:
        calls.append(schedule_id)

    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    lease = FakeLease(False)
    service = SchedulerService(store, {"health_check": handler}, scheduler=scheduler, lease=lease)
    assert not await service.start()
    assert not scheduler.running
    assert calls == []


async def test_leader_reconciles_and_rechecks_schedule_before_dispatch() -> None:
    store = FakeStore()
    store.schedules["cpu"] = {
        "id": "cpu",
        "enabled": True,
        "definition": {
            "kind": "health_check",
            "interval_minutes": 5,
            "checks": [{"id": "cpu", "expression": "up", "operator": "lt", "threshold": 1}],
        },
    }
    calls: list[str] = []

    async def handler(schedule_id: str, definition: dict[str, Any]) -> None:
        calls.append(schedule_id)

    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    lease = FakeLease()
    service = SchedulerService(store, {"health_check": handler}, scheduler=scheduler, lease=lease)
    assert await service.start()
    assert scheduler.get_job("user:cpu") is not None
    await service.run_schedule("cpu")
    assert calls == ["cpu"]
    store.schedules["cpu"]["enabled"] = False
    await service.run_schedule("cpu")
    assert calls == ["cpu"]
    await service.sync_schedule("cpu")
    assert scheduler.get_job("user:cpu") is None
    await service.stop()
    assert lease.released


async def test_internal_jobs_are_registered_once() -> None:
    store = FakeStore()
    calls: list[str] = []

    async def noop(schedule_id: str, definition: dict[str, Any]) -> None:
        pass

    async def retention() -> None:
        calls.append("retention")

    async def expiry() -> None:
        calls.append("expiry")

    jobs: dict[str, Callable[[], Awaitable[None]]] = {"retention": retention, "expiry": expiry}
    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    service = SchedulerService(
        store, {"health_check": noop}, internal_jobs=jobs, scheduler=scheduler, lease=FakeLease()
    )
    assert await service.start()
    assert scheduler.get_job("internal:retention") is not None
    assert scheduler.get_job("internal:expiry") is not None
    await service.stop()


async def test_lost_lease_stops_dispatch_and_scheduler() -> None:
    store = FakeStore()
    store.schedules["cpu"] = {
        "id": "cpu",
        "enabled": True,
        "definition": {
            "kind": "health_check",
            "interval_minutes": 5,
            "checks": [{"id": "up", "expression": "up", "operator": "lt", "threshold": 1}],
        },
    }
    calls: list[str] = []

    async def handler(schedule_id: str, _definition: dict[str, Any]) -> None:
        calls.append(schedule_id)

    lease = FakeLease()
    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    service = SchedulerService(store, {"health_check": handler}, scheduler=scheduler, lease=lease)
    assert await service.start()
    lease.healthy = False
    await service.run_schedule("cpu")
    assert calls == []
    assert not scheduler.running
    assert not service.is_leader


async def test_reconcile_does_not_postpone_existing_health_job() -> None:
    store = FakeStore()
    store.schedules["slow"] = {
        "id": "slow",
        "enabled": True,
        "definition": {
            "kind": "health_check",
            "interval_minutes": 120,
            "checks": [{"id": "up", "expression": "up", "operator": "lt", "threshold": 1}],
        },
    }

    async def noop(_schedule_id: str, _definition: dict[str, Any]) -> None:
        pass

    service = SchedulerService(
        store,
        {"health_check": noop},
        scheduler=AsyncIOScheduler(jobstores={"default": MemoryJobStore()}),
        lease=FakeLease(),
    )
    assert await service.start()
    original = service.scheduler.get_job("user:slow")
    assert original is not None
    next_run = original.next_run_time
    await service.reconcile()
    current = service.scheduler.get_job("user:slow")
    assert current is not None
    assert current is original
    assert current.next_run_time == next_run
    store.schedules["slow"]["definition"]["interval_minutes"] = 60
    await service.reconcile()
    changed = service.scheduler.get_job("user:slow")
    assert changed is not None and changed is not current
    await service.stop()


async def test_standby_promotes_without_manual_start():
    lease = FakeLease(False)
    service = SchedulerService(
        FakeStore(),
        {},
        scheduler=AsyncIOScheduler(jobstores={"default": MemoryJobStore()}),
        lease=lease,
        election_interval_seconds=0.01,
    )
    assert not await service.start()
    lease.available = True
    import asyncio

    for _ in range(100):
        if service.is_leader:
            break
        await asyncio.sleep(0.01)
    assert service.is_leader
    await service.stop()


async def test_lost_leader_reacquires_with_fresh_scheduler():
    import asyncio

    lease = FakeLease()
    service = SchedulerService(
        FakeStore(),
        {},
        scheduler=AsyncIOScheduler(jobstores={"default": MemoryJobStore()}),
        lease=lease,
        election_interval_seconds=0.01,
    )
    assert await service.start()
    original = service.scheduler
    lease.healthy = False
    lease.available = False
    for _ in range(100):
        if not service.is_leader:
            break
        await asyncio.sleep(0.01)
    assert not service.is_leader
    lease.healthy = True
    lease.available = True
    for _ in range(100):
        if service.is_leader:
            break
        await asyncio.sleep(0.01)
    assert service.is_leader and service.scheduler is not original
    await service.stop()
