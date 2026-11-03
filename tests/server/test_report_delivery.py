from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from tests.server.finance_app import (
    REQUEST,
    FixtureTools,
    client_for,
    fixture_context,
    make_storage,
)

from fin_graph.delivery.client import SlackClient
from fin_graph.delivery.service import DeliveryService, DeliveryStatus
from fin_graph.harness.runtime import make_analysis_graph
from fin_graph.scheduler.market import MarketJobs
from fin_graph.server.app import create_app
from fin_graph.server.config import ServerConfig
from fin_graph.server.manager import RunManager
from fin_graph.server.security import WebhookVerifier
from fin_graph.server.storage import Storage
from fin_graph.workflows.analysis.services import UnavailableAnalysisServices

OPERATOR = {"x-api-key": "operator-key"}


def slack(sent: list[dict[str, Any]], storage: Storage) -> DeliveryService:
    def receive(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": "123.45"})

    return DeliveryService(
        SlackClient("offline", transport=httpx.MockTransport(receive)), ledger=storage
    )


@pytest.fixture
async def delivery_stack(tmp_path: Path) -> AsyncIterator[tuple[RunManager, list[dict[str, Any]]]]:
    engine, storage = await make_storage(tmp_path / "delivery.sqlite")
    sent: list[dict[str, Any]] = []
    graph = make_analysis_graph(
        UnavailableAnalysisServices(),
        FixtureTools(),
        checkpointer=InMemorySaver(),
        delivery=slack(sent, storage),
    )
    manager = RunManager(graph, storage, fixture_context())
    try:
        yield manager, sent
    finally:
        await manager.stop()
        await engine.dispose()


async def test_an_api_analysis_delivers_only_to_explicit_destinations(delivery_stack) -> None:
    manager, sent = delivery_stack
    app = create_app(manager=manager, config=ServerConfig(_env_file=None))
    async with client_for(app) as client:
        response = await client.post(
            "/v1/analyses",
            headers=OPERATOR,
            json={"request": REQUEST, "destinations": {"slack": "C-api"}},
        )
        assert response.status_code == 202, response.text
        await manager.wait(response.json()["run_id"])
        snapshot = await manager.graph.aget_state(manager._config(response.json()["thread_id"]))
        assert snapshot.values["report"]
        assert [message["channel"] for message in sent] == ["C-api"]
        assert "DEEP" in sent[0]["text"]

        # Destinations inside the request body are data, never routing.
        response = await client.post(
            "/v1/analyses",
            headers=OPERATOR,
            json={"request": {**REQUEST, "destinations": {"slack": "C-attacker"}}},
        )
        await manager.wait(response.json()["run_id"])
        assert [message["channel"] for message in sent] == ["C-api"]


@pytest.mark.parametrize(("source", "channel"), [("tradingview", "C-tv"), ("generic", "C-default")])
async def test_signals_route_reports_from_operator_config_only(
    delivery_stack, source: str, channel: str
) -> None:
    manager, sent = delivery_stack
    config = ServerConfig(
        _env_file=None,
        signal_delivery_routes={
            "default": {"slack": "C-default"},
            "tradingview": {"slack": "C-tv"},
        },
    )
    verifier = WebhookVerifier(tokens={"tradingview": "tv", "generic": "generic"})
    app = create_app(manager=manager, config=config, verifier=verifier)
    async with client_for(app) as client:
        response = await client.post(
            f"/v1/signals/{source}",
            headers={"x-fin-webhook-token": "tv" if source == "tradingview" else "generic"},
            json={
                **REQUEST,
                "exchange": "NASDAQ",
                "interval": "1D",
                "destinations": {"slack": "C-attacker"},
            },
        )
        assert response.status_code == 202, response.text
        await manager.wait(response.json()["run_id"])
        assert [message["channel"] for message in sent] == [channel]


async def test_a_market_watch_trigger_delivers_its_analysis_to_the_schedule(
    delivery_stack,
) -> None:
    manager, sent = delivery_stack
    jobs = MarketJobs(
        manager.storage,
        manager,
        fixture_context(),
        now=lambda: datetime(2024, 6, 28, 21, tzinfo=UTC),
    )

    await jobs.market_watch(
        "deep-watch",
        {
            "kind": "market_watch",
            "interval_minutes": 60,
            # A tiny threshold: any daily move on record triggers the analysis.
            "rules": [{"ticker": "DEEP", "max_daily_move_pct": 0.0001}],
            "destinations": {"slack": "C-schedule"},
        },
    )

    run = (await manager.storage.list_runs(1))[0]
    await manager.wait(run["run_id"])
    snapshot = await manager.graph.aget_state(manager._config(run["thread_id"]))
    assert snapshot.values["report"]["request"]["source"] == "price_alert"
    assert "Vigilancia de mercado" in snapshot.values["report"]["request"]["question"]
    assert [message["channel"] for message in sent] == ["C-schedule"]


@pytest.mark.parametrize(
    "destinations",
    [{"email": "trader@example.com"}, {"slack": " "}, {"slack": "C" * 129}, {"slack": 7}],
)
async def test_the_api_rejects_invalid_report_destinations(delivery_stack, destinations) -> None:
    manager, sent = delivery_stack
    app = create_app(manager=manager, config=ServerConfig(_env_file=None))
    async with client_for(app) as client:
        response = await client.post(
            "/v1/analyses",
            headers=OPERATOR,
            json={"text": "analiza DEEP", "destinations": destinations},
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
    saver = InMemorySaver()

    def restart() -> Any:
        return make_analysis_graph(
            UnavailableAnalysisServices(),
            FixtureTools(),
            checkpointer=saver,
            delivery=slack(sent, Storage(manager.storage.engine)),
        )

    graph = restart()
    config = manager._config("first-thread")
    context = fixture_context()()
    first = await graph.ainvoke(
        {
            "raw_request": REQUEST,
            **({"run_id": "first-run"} if persist_run_id else {}),
            "destinations": {"slack": "C" * 128},
        },
        config=config,
        context=context,
    )
    assert first["deliveries"][0]["status"] == DeliveryStatus.SENT
    assert len(sent) == 1
    # Re-execute the checkpoint just before delivery with a fresh service and storage object.
    graph = restart()
    await graph.aupdate_state(config, {"deliveries": []}, as_node="report")
    replay = await graph.ainvoke(None, config=config, context=context)
    assert replay["deliveries"][0]["status"] == DeliveryStatus.SENT
    assert len(sent) == 1
    second = await graph.ainvoke(
        {
            "raw_request": REQUEST,
            **({"run_id": "second-run"} if persist_run_id else {}),
            "destinations": {"slack": "C" * 128},
        },
        config=manager._config("second-thread"),
        context=context,
    )
    assert first["request"]["tickers"] == second["request"]["tickers"]
    assert len(sent) == 2
    assert sent[0]["client_msg_id"] != sent[1]["client_msg_id"]


async def test_an_uncertain_report_send_stays_blocked_after_restart(delivery_stack) -> None:
    from tests.domain.test_analysis_report import _report

    manager, _ = delivery_stack
    sent: list[httpx.Request] = []

    def receive(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        raise httpx.ReadTimeout("remote outcome unknown")

    def restart() -> DeliveryService:
        return DeliveryService(
            SlackClient("offline", transport=httpx.MockTransport(receive)),
            ledger=Storage(manager.storage.engine),
        )

    report = _report()
    first = await restart().send_analysis(report, {"slack": "C1"}, "run-uncertain")
    replay = await restart().send_analysis(report, {"slack": "C1"}, "run-uncertain")
    assert first[0].status == replay[0].status == DeliveryStatus.FAILED
    assert "uncertain" in replay[0].detail
    assert len(sent) == 1
