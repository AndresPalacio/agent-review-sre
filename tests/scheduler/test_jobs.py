from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from fin_graph.integrations.context import RunContext
from fin_graph.scheduler.jobs import HealthJobs


class Probe:
    async def query(self, expression: str) -> dict[str, Any]:
        return {"resultType": "scalar", "result": [0, "0"]}


class Store:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, dict[str, Any]]] = []
        self.claims = 0
        self.failed: list[str] = []
        self.released: list[str] = []

    async def record_health_result(
        self, schedule_id: str, status: str, details: dict[str, Any], checked_at: datetime
    ) -> None:
        self.results.append((schedule_id, status, details))

    async def recent_health_results(
        self, since: datetime, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        return [
            {"status": status, "details": details, "schedule_id": schedule_id}
            for schedule_id, status, details in self.results
        ]

    async def claim_webhook_run(self, *args: Any) -> tuple[bool, str]:
        self.claims += 1
        return (self.claims == 1, "thread-1")

    async def set_status(self, run_id: str, status: str, error: str | None = None) -> None:
        self.failed.append(status)

    async def release_thread_fingerprints(self, thread_id: str) -> None:
        self.released.append(thread_id)


class Manager:
    def __init__(self) -> None:
        self.submitted: list[str] = []

    def schedule_existing(
        self,
        run_id: str,
        thread_id: str,
        raw_alert: str,
        source_hint: str | None,
        **kwargs: Any,
    ) -> dict[str, str]:
        self.submitted.append(raw_alert)
        return {"run_id": run_id, "thread_id": thread_id}


async def test_health_job_records_anomaly_and_deduplicates_escalation() -> None:
    store, manager = Store(), Manager()
    jobs = HealthJobs(
        store,
        manager,
        lambda: RunContext(clients={"prometheus": Probe()}),
        now=lambda: datetime(2026, 10, 8, tzinfo=UTC),
    )
    definition = {
        "kind": "health_check",
        "interval_minutes": 5,
        "checks": [{"id": "up", "expression": "up", "operator": "lt", "threshold": 1}],
        "destinations": {},
    }
    await jobs.health_check("cluster", definition)
    await jobs.health_check("cluster", definition)
    assert [result[1] for result in store.results] == ["anomalous", "anomalous"]
    assert len(manager.submitted) == 1
    assert "up" in manager.submitted[0]


async def test_digest_uses_recent_results_and_only_explicit_destinations() -> None:
    store, manager = Store(), Manager()
    store.results.append(
        (
            "cluster",
            "healthy",
            {
                "checks": [
                    {"id": "up", "status": "healthy", "value": 1, "reason": "within threshold"}
                ]
            },
        )
    )
    sent: list[tuple[str, dict[str, str], str]] = []

    async def send_text(text: str, destinations: dict[str, str], key: str) -> None:
        sent.append((text, destinations, key))

    jobs = HealthJobs(
        store,
        manager,
        lambda: RunContext(),
        send_text=send_text,
        now=lambda: datetime(2026, 10, 8, 8, tzinfo=UTC),
    )
    await jobs.digest("morning", {"kind": "digest", "destinations": {"slack": "C1"}})
    assert len(sent) == 1
    assert "1 healthy" in sent[0][0]
    assert sent[0][1] == {"slack": "C1"}


async def test_missing_prometheus_is_unknown_and_never_escalates() -> None:
    store, manager = Store(), Manager()
    jobs = HealthJobs(
        store, manager, lambda: RunContext(), now=lambda: datetime(2026, 10, 8, tzinfo=UTC)
    )
    await jobs.health_check(
        "cluster",
        {
            "kind": "health_check",
            "interval_minutes": 5,
            "checks": [{"id": "up", "expression": "up", "operator": "lt", "threshold": 1}],
            "destinations": {},
        },
    )
    assert store.results[0][1] == "unknown"
    assert manager.submitted == []


async def test_schedule_failure_releases_claimed_anomaly() -> None:
    store = Store()

    class FailingManager(Manager):
        def schedule_existing(self, *args: Any, **kwargs: Any) -> dict[str, str]:
            raise RuntimeError("worker unavailable")

    jobs = HealthJobs(store, FailingManager(), lambda: RunContext(clients={"prometheus": Probe()}))
    with pytest.raises(RuntimeError, match="worker unavailable"):
        await jobs.health_check(
            "cluster",
            {
                "kind": "health_check",
                "interval_minutes": 5,
                "checks": [{"id": "up", "expression": "up", "operator": "lt", "threshold": 1}],
                "destinations": {},
            },
        )
    assert store.failed == ["failed"]
    assert len(store.released) == 1
