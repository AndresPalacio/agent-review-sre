"""Single-leader APScheduler service with durable, ID-only jobs."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC
from threading import RLock
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from fin_graph.delivery.settings import validate_destinations

_LOCK_ID = 0x46494E4752415048  # "FINGRAPH", stable across restarts.
_ACTIVE_SERVICE: SchedulerService | None = None
_LOG = logging.getLogger(__name__)


class ScheduleStore(Protocol):
    async def get_schedule(self, schedule_id: str) -> dict[str, Any] | None: ...

    async def list_schedules(self) -> list[dict[str, Any]]: ...


class LeaderLease(Protocol):
    def acquire(self) -> bool: ...

    def held(self) -> bool: ...

    def release(self) -> None: ...


class PgAdvisoryLease:
    """Hold a dedicated connection while this process owns the scheduler."""

    def __init__(self, database_url: str) -> None:
        self.engine: Engine = create_engine(database_url, pool_pre_ping=True)
        self.connection: Connection | None = None
        self.backend_pid: int | None = None
        self._mutex = RLock()

    def acquire(self) -> bool:
        with self._mutex:
            if self.connection is not None:
                return self._held_unlocked()
            connection = self.engine.connect()
            try:
                acquired = connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": _LOCK_ID}
                ).scalar_one()
                backend_pid = (
                    int(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
                    if acquired
                    else None
                )
                connection.commit()
            except Exception:
                connection.close()
                raise
            if not acquired:
                connection.close()
                return False
            self.connection = connection
            self.backend_pid = backend_pid
            return True

    def held(self) -> bool:
        with self._mutex:
            return self._held_unlocked()

    def _held_unlocked(self) -> bool:
        if self.connection is None or self.backend_pid is None:
            return False
        try:
            pid = self.connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            self.connection.commit()
            return bool(pid == self.backend_pid)
        except Exception:
            return False

    def release(self) -> None:
        with self._mutex:
            if self.connection is not None:
                try:
                    if self._held_unlocked():
                        self.connection.execute(
                            text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": _LOCK_ID}
                        )
                        self.connection.commit()
                finally:
                    self.connection.close()
                    self.connection = None
                    self.backend_pid = None
            self.engine.dispose()


def validate_schedule(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize the public schedule definition before it reaches the job store."""
    if not isinstance(value, dict):
        raise ValueError("schedule definition must be an object")
    kind = value.get("kind")
    destinations = value.get("destinations", {})
    if not isinstance(destinations, dict) or any(
        channel not in {"slack", "telegram"}
        or not isinstance(destination, str)
        or not 1 <= len(destination) <= 64
        for channel, destination in destinations.items()
    ):
        raise ValueError("invalid schedule destinations")
    destinations = validate_destinations(destinations)
    if kind == "health_check":
        minutes = value.get("interval_minutes")
        checks = value.get("checks")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 1440:
            raise ValueError("interval_minutes must be between 1 and 1440")
        if not isinstance(checks, list) or not 1 <= len(checks) <= 20:
            raise ValueError("health_check requires 1 to 20 checks")
        ids: set[str] = set()
        normalized = []
        for check in checks:
            if not isinstance(check, dict):
                raise ValueError("check must be an object")
            check_id, expression = check.get("id"), check.get("expression")
            operator, threshold = check.get("operator"), check.get("threshold")
            if (
                not isinstance(check_id, str)
                or not 1 <= len(check_id) <= 64
                or check_id in ids
                or not isinstance(expression, str)
                or not 1 <= len(expression) <= 1000
                or operator not in {"gt", "gte", "lt", "lte"}
                or not isinstance(threshold, (int, float))
                or isinstance(threshold, bool)
                or not math.isfinite(threshold)
            ):
                raise ValueError("invalid health check")
            ids.add(check_id)
            normalized.append(
                {
                    "id": check_id,
                    "expression": expression,
                    "operator": operator,
                    "threshold": float(threshold),
                }
            )
        return {
            "kind": kind,
            "interval_minutes": minutes,
            "checks": normalized,
            "destinations": destinations,
        }
    if kind == "digest":
        hour, minute, zone = value.get("hour"), value.get("minute"), value.get("timezone")
        if (
            not isinstance(hour, int)
            or isinstance(hour, bool)
            or not 0 <= hour <= 23
            or not isinstance(minute, int)
            or isinstance(minute, bool)
            or not 0 <= minute <= 59
            or not isinstance(zone, str)
        ):
            raise ValueError("invalid digest time")
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("invalid digest timezone") from exc
        return {
            "kind": kind,
            "hour": hour,
            "minute": minute,
            "timezone": zone,
            "destinations": destinations,
        }
    raise ValueError("unsupported schedule kind")


async def run_scheduled_job(schedule_id: str) -> None:
    """Module-level function: APScheduler can serialize this reference safely."""
    if _ACTIVE_SERVICE is not None:
        await _ACTIVE_SERVICE.run_schedule(schedule_id)


async def run_internal_job(kind: str) -> None:
    if _ACTIVE_SERVICE is not None:
        await _ACTIVE_SERVICE.run_internal(kind)


class SchedulerService:
    def __init__(
        self,
        storage: ScheduleStore,
        handlers: Mapping[str, Callable[[str, dict[str, Any]], Awaitable[None]]],
        *,
        internal_jobs: Mapping[str, Callable[[], Awaitable[None]]] | None = None,
        scheduler: AsyncIOScheduler | None = None,
        lease: LeaderLease | None = None,
        database_url: str | None = None,
        election_interval_seconds: float = 3,
    ) -> None:
        if election_interval_seconds <= 0:
            raise ValueError("election interval must be positive")
        injected_scheduler = scheduler
        if scheduler is None:
            if database_url is None:
                raise ValueError("database_url is required for persistent scheduler")
            scheduler = AsyncIOScheduler(
                jobstores={"default": SQLAlchemyJobStore(url=database_url)}, timezone=UTC
            )
        if lease is None:
            if database_url is None:
                raise ValueError("database_url is required for scheduler leadership")
            lease = PgAdvisoryLease(database_url)
        self.storage = storage
        self.handlers = handlers
        self.internal_jobs = internal_jobs or {}
        self.scheduler = scheduler
        self.lease = lease
        self.election_interval_seconds = election_interval_seconds
        self._supervisor: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._needs_scheduler = False
        self._scheduler_factory: Callable[[], AsyncIOScheduler] = (
            (
                lambda: AsyncIOScheduler(
                    jobstores={"default": SQLAlchemyJobStore(url=database_url)}, timezone=UTC
                )
            )
            if injected_scheduler is None
            else (
                lambda: AsyncIOScheduler(
                    jobstores=dict(injected_scheduler._jobstores), timezone=UTC
                )
            )
        )
        self.is_leader = False
        self._applied: dict[str, dict[str, Any]] = {}

    async def start(self) -> bool:
        async with self._lifecycle_lock:
            if self._supervisor is not None:
                if not self.is_leader:
                    return await self._promote()
                return self.is_leader
            try:
                promoted = await self._promote()
            except Exception:
                _LOG.warning("scheduler promotion failed; retrying")
                promoted = False
            self._supervisor = asyncio.create_task(self._elect())
            return promoted

    async def _elect(self) -> None:
        while True:
            await asyncio.sleep(self.election_interval_seconds)
            try:
                async with self._lifecycle_lock:
                    if self.is_leader:
                        await self._owns_leadership()
                    if not self.is_leader:
                        await self._promote()
            except Exception:
                _LOG.warning("scheduler election failed; retrying")

    async def _promote(self) -> bool:
        global _ACTIVE_SERVICE
        acquisition = asyncio.create_task(asyncio.to_thread(self.lease.acquire))
        try:
            acquired = await asyncio.shield(acquisition)
        except asyncio.CancelledError:
            await acquisition
            await asyncio.to_thread(self.lease.release)
            raise
        if not acquired:
            return False
        try:
            if self._needs_scheduler:
                self.scheduler = self._scheduler_factory()
                self._needs_scheduler = False
            _ACTIVE_SERVICE = self
            self.scheduler.start(paused=True)
            for kind in self.internal_jobs:
                interval = (
                    60
                    if kind in {"reconcile", "gateway_replay"}
                    else 300
                    if kind == "expiry"
                    else 3600
                )
                self.scheduler.add_job(
                    run_internal_job,
                    IntervalTrigger(seconds=interval, timezone=UTC),
                    id=f"internal:{kind}",
                    args=[kind],
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=60,
                )
            self.is_leader = True
            await self.reconcile()
            self.scheduler.resume()
            return True
        except BaseException:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
                self._needs_scheduler = True
            _ACTIVE_SERVICE = None
            self.is_leader = False
            await asyncio.to_thread(self.lease.release)
            raise

    async def stop(self) -> None:
        supervisor, self._supervisor = self._supervisor, None
        if supervisor is not None:
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)
        async with self._lifecycle_lock:
            await self._demote()

    async def _demote(self) -> None:
        global _ACTIVE_SERVICE
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self._needs_scheduler = True
            await asyncio.sleep(0)
        if _ACTIVE_SERVICE is self:
            _ACTIVE_SERVICE = None
        self.is_leader = False
        self._applied.clear()
        await asyncio.to_thread(self.lease.release)

    async def _owns_leadership(self) -> bool:
        if not self.is_leader:
            return False
        if await asyncio.to_thread(self.lease.held):
            return True
        await self._demote()
        return False

    async def reconcile(self) -> None:
        if not await self._owns_leadership():
            return
        rows = await self.storage.list_schedules()
        known = {f"user:{row['id']}" for row in rows if row["enabled"]}
        for job in self.scheduler.get_jobs():
            if job.id.startswith("user:") and job.id not in known:
                self.scheduler.remove_job(job.id)
                self._applied.pop(job.id.removeprefix("user:"), None)
        for row in rows:
            await self.sync_schedule(row["id"])

    async def sync_schedule(self, schedule_id: str) -> None:
        if not await self._owns_leadership():
            return
        row = await self.storage.get_schedule(schedule_id)
        job_id = f"user:{schedule_id}"
        if row is None or not row["enabled"]:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            self._applied.pop(schedule_id, None)
            return
        definition = validate_schedule(row["definition"])
        if self._applied.get(schedule_id) == definition and self.scheduler.get_job(job_id):
            return
        trigger = (
            IntervalTrigger(minutes=definition["interval_minutes"], timezone=UTC)
            if definition["kind"] == "health_check"
            else CronTrigger(
                hour=definition["hour"],
                minute=definition["minute"],
                timezone=ZoneInfo(definition["timezone"]),
            )
        )
        self.scheduler.add_job(
            run_scheduled_job,
            trigger,
            id=job_id,
            args=[schedule_id],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
        self._applied[schedule_id] = definition

    async def run_schedule(self, schedule_id: str) -> None:
        if not await self._owns_leadership():
            return
        row = await self.storage.get_schedule(schedule_id)
        if row is None or not row["enabled"]:
            return
        definition = validate_schedule(row["definition"])
        await self.handlers[definition["kind"]](schedule_id, definition)

    async def run_internal(self, kind: str) -> None:
        if await self._owns_leadership():
            await self.internal_jobs[kind]()
