from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI

from fin_graph.adapters.reference_app.http import reference_app_router
from fin_graph.adapters.reference_app.settings import ReferenceAppProfile
from fin_graph.backtesting import BacktestArtifactStore, BacktestJobManager, BacktestRunOutput
from fin_graph.backtesting.models import BacktestEvent, BacktestEventKind
from fin_graph.persistence.reference_app import FernetSecretCipher, open_reference_sqlite


def config(symbol: str = "AAPL") -> dict[str, object]:
    return {
        "algorithm_key": "sma_crossover",
        "parameters": {"fast_window": 2, "slow_window": 5},
        "symbols": [symbol],
        "start_date": "2025-01-01",
        "end_date": "2025-01-31",
        "initial_capital": 10000,
    }


async def runner(request: dict[str, object], *, event_sink: object, cancellation: asyncio.Event):
    if request["symbols"] == ["SLOW"]:
        await cancellation.wait()
    else:
        await event_sink(  # type: ignore[operator]
            BacktestEvent(
                sequence=0,
                kind=BacktestEventKind.PROGRESS,
                progress=0.5,
                message="halfway",
            )
        )
    return BacktestRunOutput(
        result={
            "metrics": {"total_return": 0.1, "trade_count": 1},
            "equity_curve": [],
            "trades": [],
            "signal_summary": [],
            "data_coverage": {},
            "warnings": [],
        },
        signals=(
            {
                "date": "2025-01-02",
                "symbol": str(request["symbols"][0]),
                "raw_signal": 0.5,
                "applied_weight": 0.0,
            },
            {
                "date": "2025-01-03",
                "symbol": str(request["symbols"][0]),
                "raw_signal": 0.7,
                "applied_weight": 0.5,
            },
        ),
    )


@asynccontextmanager
async def client_for(
    database: Path, artifacts: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, BacktestJobManager]]:
    cipher = FernetSecretCipher(Fernet.generate_key())
    async with open_reference_sqlite(database, cipher) as store:
        manager = BacktestJobManager(store, BacktestArtifactStore(artifacts), {"strategy": runner})
        app = FastAPI()
        app.state.reference_app_profile = ReferenceAppProfile()
        app.state.reference_app_store = store
        app.state.reference_app_backtest_manager = manager
        app.include_router(reference_app_router())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, manager
        await manager.close()


async def wait_for(client: httpx.AsyncClient, run_id: str, status: str) -> dict[str, object]:
    for _ in range(200):
        response = await client.get(f"/v2/backtests/{run_id}")
        if response.json()["status"] == status:
            return response.json()
        await asyncio.sleep(0.005)
    raise AssertionError(f"job never reached {status}")


async def test_algorithm_job_result_signal_pagination_events_and_restart(tmp_path: Path) -> None:
    database, artifacts = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    async with client_for(database, artifacts) as (client, _manager):
        started = await client.post("/v2/backtests", json=config())
        assert started.status_code == 202
        run_id = started.json()["run_id"]
        completed = await wait_for(client, run_id, "complete")
        page = await client.get(f"/v2/backtests/{run_id}/signals", params={"offset": 0, "limit": 1})
        stream = await client.get(f"/v2/backtests/{run_id}/events")

        assert completed["result"]["metrics"]["total_return"] == 0.1
        assert page.json()["next_offset"] == 1 and page.json()["total"] == 2
        assert "event: progress" in stream.text and "event: complete" in stream.text
        assert sum("event: complete" in frame for frame in stream.text.split("\n\n")) == 1

    async with client_for(database, artifacts) as (restarted, _manager):
        persisted = await restarted.get(f"/v2/backtests/{run_id}")
        assert persisted.json()["status"] == "complete"


async def test_algorithm_job_validation_duplicate_requests_and_idempotent_cancel(
    tmp_path: Path,
) -> None:
    async with client_for(tmp_path / "cancel.sqlite", tmp_path / "artifacts") as (
        client,
        _manager,
    ):
        invalid_algorithm = await client.post(
            "/v2/backtests", json=config() | {"algorithm_key": "missing"}
        )
        invalid_parameter = await client.post(
            "/v2/backtests", json=config() | {"parameters": {"unknown": 1}}
        )
        first = await client.post("/v2/backtests", json=config())
        second = await client.post("/v2/backtests", json=config())
        slow = await client.post("/v2/backtests", json=config("SLOW"))
        slow_id = slow.json()["run_id"]
        cancelled = await client.post(f"/v2/backtests/{slow_id}/cancel")
        terminal = await wait_for(client, slow_id, "cancelled")
        cancelled_again = await client.post(f"/v2/backtests/{slow_id}/cancel")

    assert invalid_algorithm.status_code == 404
    assert invalid_parameter.status_code == 422
    assert first.json()["run_id"] != second.json()["run_id"]
    assert cancelled.status_code == 200
    assert terminal["status"] == cancelled_again.json()["status"] == "cancelled"
