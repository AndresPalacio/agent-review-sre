from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.delivery.client import SlackClient
from fin_graph.delivery.service import DeliveryService, DeliveryStatus
from fin_graph.domain.alerts.model import AlertSource, NormalizedAlert
from fin_graph.domain.diagnosis import unknown_diagnosis
from fin_graph.domain.report import Report
from fin_graph.graphs.investigation.graph import make_investigation_graph
from fin_graph.graphs.investigation.services import UnavailableServices
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.prometheus.client import PrometheusClient
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.scheduler.jobs import HealthJobs
from fin_graph.server.app import create_app
from fin_graph.server.config import ServerConfig
from fin_graph.server.manager import RunManager
from fin_graph.server.security import WebhookVerifier
from fin_graph.server.storage import Base, Storage


@pytest.fixture
async def delivery_stack(tmp_path: Path) -> AsyncIterator[tuple[RunManager, list[dict[str, Any]]]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'delivery.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("operator", "operator")
    sent: list[dict[str, Any]] = []

    def receive(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": "123.45"})

    delivery = DeliveryService(
        SlackClient("offline", transport=httpx.MockTransport(receive)), ledger=storage
    )
    graph = make_investigation_graph(
        UnavailableServices(),
        IntegrationRegistry([], env_file=None),
        checkpointer=InMemorySaver(),
        delivery=delivery,
    )
    manager = RunManager(graph, storage, RunContext)
    try:
        yield manager, sent
    finally:
        await manager.stop()
        await engine.dispose()


async def test_api_investigation_delivers_only_to_explicit_destinations(delivery_stack) -> None:
    manager, sent = delivery_stack
    app = create_app(manager=manager, config=ServerConfig(_env_file=None))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post(
            "/v1/investigations",
            headers={"x-api-key": "operator"},
            json={"text": "checkout API is down", "destinations": {"slack": "C-api"}},
        )
        assert response.status_code == 202, response.text
        await manager.wait(response.json()["run_id"])
        snapshot = await manager.graph.aget_state(manager._config(response.json()["thread_id"]))
        assert snapshot.values["report"]
        assert [message["channel"] for message in sent] == ["C-api"]
        assert snapshot.values["delivery_destinations"] == {"slack": "C-api"}

        response = await client.post(
            "/v1/investigations",
            headers={"x-api-key": "operator"},
            json={"alert": {"title": "API down", "destinations": {"slack": "C-attacker"}}},
        )
        await manager.wait(response.json()["run_id"])
        assert [message["channel"] for message in sent] == ["C-api"]


@pytest.mark.parametrize("severity", ["critical", "info"])
async def test_webhook_routes_severity_from_operator_config_only(delivery_stack, severity) -> None:
    manager, sent = delivery_stack
    config = ServerConfig(
        _env_file=None,
        alert_delivery_routes={
            "default": {"slack": "C-default"},
            "critical": {"slack": "C-critical"},
        },
    )
    app = create_app(
        manager=manager,
        config=config,
        verifier=WebhookVerifier(tokens={"generic": "webhook"}),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post(
            "/v1/alerts/generic",
            headers={"x-sre-webhook-token": "webhook"},
            json={
                "title": "checkout API down",
                "severity": severity,
                "destinations": {"slack": "C-attacker"},
                "labels": {"slack_channel": "C-attacker"},
            },
        )
        assert response.status_code == 202, response.text
        await manager.wait(response.json()["run_id"])
        expected = "C-critical" if severity == "critical" else "C-default"
        assert [message["channel"] for message in sent] == [expected]


async def test_scheduled_anomaly_delivers_report_to_schedule_destination(delivery_stack) -> None:
    manager, sent = delivery_stack
    probe = PrometheusClient(
        "https://prometheus.invalid",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"status": "success", "data": {"resultType": "scalar", "result": [0, "0"]}},
            )
        ),
    )
    jobs = HealthJobs(
        manager.storage,
        manager,
        lambda: RunContext(clients={"prometheus": probe}),
        now=lambda: datetime(2026, 10, 8, tzinfo=UTC),
    )
    await jobs.health_check(
        "cluster",
        {
            "kind": "health_check",
            "interval_minutes": 5,
            "checks": [{"id": "up", "expression": "up", "operator": "lt", "threshold": 1}],
            "destinations": {"slack": "C-schedule"},
        },
    )
    run = (await manager.storage.list_runs(1))[0]
    await manager.wait(run["run_id"])
    snapshot = await manager.graph.aget_state(manager._config(run["thread_id"]))
    assert snapshot.values["report"]
    assert [message["channel"] for message in sent] == ["C-schedule"]


@pytest.mark.parametrize(
    "destinations",
    [{"email": "ops@example.com"}, {"slack": " "}, {"slack": "C" * 129}, {"slack": 7}],
)
async def test_api_rejects_invalid_report_destinations(delivery_stack, destinations) -> None:
    manager, sent = delivery_stack
    app = create_app(manager=manager, config=ServerConfig(_env_file=None))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post(
            "/v1/investigations",
            headers={"x-api-key": "operator"},
            json={"text": "API down", "destinations": destinations},
        )
        assert response.status_code == 422
        assert await manager.storage.list_runs(1) == []
        assert sent == []


@pytest.mark.parametrize("persist_run_id", [True, False])
async def test_report_replay_after_restart_is_deduplicated_but_new_run_sends(
    delivery_stack, persist_run_id: bool
) -> None:
    manager, _ = delivery_stack
    sent: list[dict[str, Any]] = []

    def receive(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": "123.45"})

    saver = InMemorySaver()

    def restart():
        return make_investigation_graph(
            UnavailableServices(),
            IntegrationRegistry([], env_file=None),
            checkpointer=saver,
            delivery=DeliveryService(
                SlackClient("offline", transport=httpx.MockTransport(receive)),
                ledger=Storage(manager.storage.engine),
            ),
        )

    graph = restart()
    config = manager._config("first-thread")
    first = await graph.ainvoke(
        {
            "raw_alert": {"title": "API down"},
            **({"run_id": "first-run"} if persist_run_id else {}),
            "destinations": {"slack": "C" * 128},
        },
        config=config,
        context=RunContext(),
    )
    assert first["deliveries"][0]["status"] == DeliveryStatus.SENT
    assert len(sent) == 1
    # Re-execute the checkpoint just before delivery with a fresh service/storage object.
    graph = restart()
    await graph.aupdate_state(config, {"deliveries": []}, as_node="report")
    replay = await graph.ainvoke(None, config=config, context=RunContext())
    assert replay["deliveries"][0]["status"] == DeliveryStatus.SENT
    assert len(sent) == 1
    second = await graph.ainvoke(
        {
            "raw_alert": {"title": "API down"},
            **({"run_id": "second-run"} if persist_run_id else {}),
            "destinations": {"slack": "C" * 128},
        },
        config=manager._config("second-thread"),
        context=RunContext(),
    )
    assert first["alert"]["fingerprint"] == second["alert"]["fingerprint"]
    assert len(sent) == 2
    assert sent[0]["client_msg_id"] != sent[1]["client_msg_id"]


async def test_uncertain_report_send_remains_blocked_after_restart(delivery_stack) -> None:
    manager, _ = delivery_stack
    sent: list[httpx.Request] = []

    def receive(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        raise httpx.ReadTimeout("remote outcome unknown")

    alert = NormalizedAlert(source=AlertSource.GENERIC, title="API down", fingerprint="same")
    report = Report(alert=alert, diagnosis=unknown_diagnosis(alert, "no evidence"))

    def restart():
        return DeliveryService(
            SlackClient("offline", transport=httpx.MockTransport(receive)),
            ledger=Storage(manager.storage.engine),
        )

    first = await restart().send_report(report, {"slack": "C1"}, "run-uncertain")
    replay = await restart().send_report(report, {"slack": "C1"}, "run-uncertain")
    assert first[0].status == replay[0].status == DeliveryStatus.FAILED
    assert "uncertain" in replay[0].detail
    assert len(sent) == 1
