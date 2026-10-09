"""Read-only scheduled health checks, escalation and morning digest."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from sre_graph.graphs.health_check.graph import HealthCheckResult, make_health_check_graph
from sre_graph.integrations.context import RunContext


class HealthStorage(Protocol):
    async def record_health_result(
        self, schedule_id: str, status: str, details: dict[str, Any], checked_at: datetime
    ) -> None: ...

    async def recent_health_results(
        self, since: datetime, *, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    async def claim_webhook_run(
        self,
        source: str,
        fingerprint: str,
        thread_id: str,
        run_id: str,
        now: datetime,
        window: timedelta,
    ) -> tuple[bool, str]: ...

    async def set_status(self, run_id: str, status: str, error: str | None = None) -> None: ...

    async def release_thread_fingerprints(self, thread_id: str) -> None: ...


class InvestigationManager(Protocol):
    def schedule_existing(
        self,
        run_id: str,
        thread: str,
        raw_alert: str,
        source_hint: str | None,
        *,
        destinations: dict[str, str] | None = None,
        user_requested: bool = True,
    ) -> dict[str, Any]: ...


class _ContextProbe:
    def __init__(self, factory: Callable[[], RunContext]) -> None:
        self.factory = factory

    async def query(self, expression: str) -> Any:
        client = self.factory().clients.get("prometheus")
        query = getattr(client, "query", None)
        if query is None:
            raise RuntimeError("Prometheus is not configured")
        return await query(expression)


class HealthJobs:
    def __init__(
        self,
        storage: HealthStorage,
        manager: InvestigationManager,
        context_factory: Callable[[], RunContext],
        *,
        send_text: Callable[[str, dict[str, str], str], Awaitable[None]] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.storage = storage
        self.manager = manager
        self.send_text = send_text
        self.now = now
        self.graph = make_health_check_graph(_ContextProbe(context_factory))

    async def health_check(self, schedule_id: str, definition: dict[str, Any]) -> None:
        result = await self.graph.ainvoke({"definitions": definition["checks"]})
        checks = [HealthCheckResult.model_validate(item) for item in result["results"]]
        status = (
            "anomalous"
            if any(item.status == "anomalous" for item in checks)
            else "unknown"
            if any(item.status == "unknown" for item in checks)
            else "healthy"
        )
        moment = self.now()
        await self.storage.record_health_result(
            schedule_id,
            status,
            {"checks": [item.model_dump(mode="json") for item in checks]},
            moment,
        )
        if not result["escalate"]:
            return
        anomalous = [item.id for item in checks if item.status == "anomalous"]
        fingerprint = hashlib.sha256(f"{schedule_id}:{','.join(anomalous)}".encode()).hexdigest()
        run_id, thread_id = str(uuid4()), str(uuid4())
        claimed, _ = await self.storage.claim_webhook_run(
            "scheduler",
            fingerprint,
            thread_id,
            run_id,
            moment,
            timedelta(minutes=definition["interval_minutes"]),
        )
        if claimed:
            text = f"Scheduled health anomaly in {schedule_id}: {', '.join(anomalous)}"
            try:
                self.manager.schedule_existing(
                    run_id,
                    thread_id,
                    text,
                    None,
                    destinations=definition.get("destinations", {}),
                    user_requested=False,
                )
            except Exception:
                await self.storage.set_status(run_id, "failed", "schedule_unavailable")
                await self.storage.release_thread_fingerprints(thread_id)
                raise

    async def digest(self, schedule_id: str, definition: dict[str, Any]) -> None:
        recent_rows = await self.storage.recent_health_results(
            self.now() - timedelta(days=1), limit=100
        )
        checks = [
            check for row in recent_rows for check in row.get("details", {}).get("checks", [])
        ]
        result = await self.graph.ainvoke({"definitions": [], "recent_results": checks})
        destinations: dict[str, str] = definition.get("destinations", {})
        if not destinations:
            return
        if self.send_text is None:
            raise RuntimeError("digest delivery is unavailable")
        local_day = self.now().astimezone(ZoneInfo(definition.get("timezone", "UTC"))).date()
        key = hashlib.sha256(f"digest:{schedule_id}:{local_day}".encode()).hexdigest()
        await self.send_text(result["digest"], destinations, key)
