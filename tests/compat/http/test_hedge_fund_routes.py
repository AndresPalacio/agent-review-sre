from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from fin_graph.compat.ai_hedge_fund.schemas import BacktestRequest, HedgeFundRequest
from fin_graph.compat.settings import CompatibilityProfile
from fin_graph.server.compat import compatibility_router
from fin_graph.server.compat.events import InternalEvent


def request_body() -> dict[str, Any]:
    return {
        "tickers": ["AAPL"],
        "graph_nodes": [
            {"id": "stock-analyzer-node", "type": "stock-analyzer-node"},
            {"id": "valuation_analyst", "type": "agent-node"},
            {"id": "portfolio_manager", "type": "portfolio-manager-node"},
        ],
        "graph_edges": [
            {"id": "a", "source": "stock-analyzer-node", "target": "valuation_analyst"},
            {"id": "b", "source": "valuation_analyst", "target": "portfolio_manager"},
        ],
        "start_date": "2025-01-01",
        "end_date": "2025-01-31",
        "initial_cash": 10000,
    }


class FakeRunner:
    async def run(
        self,
        request: HedgeFundRequest,
        emit: Callable[[InternalEvent], Awaitable[None]],
    ) -> dict[str, object]:
        await emit(
            InternalEvent(
                kind="progress",
                payload={
                    "agent": "valuation_analyst",
                    "ticker": "AAPL",
                    "status": "complete",
                    "timestamp": None,
                    "analysis": "neutral",
                },
            )
        )
        return {
            "decisions": {"AAPL": {"action": "hold", "quantity": 0}},
            "analyst_signals": {"valuation_analyst": {"AAPL": {"signal": "neutral"}}},
            "current_prices": {"AAPL": 100.0},
        }

    async def backtest(
        self,
        request: BacktestRequest,
        emit: Callable[[InternalEvent], Awaitable[None]],
        cancellation: object,
    ) -> dict[str, object]:
        return {}


@asynccontextmanager
async def client_for(runner: object) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.compatibility_profile = CompatibilityProfile()
    app.state.compatibility_store = None
    app.state.compatibility_hedge_fund = runner
    app.include_router(compatibility_router())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def events(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    values = []
    for frame in response.text.strip().split("\n\n"):
        event_line, data_line = frame.splitlines()
        values.append((event_line.removeprefix("event: "), json.loads(data_line[6:])))
    return values


async def test_agents_and_hedge_fund_stream_contract() -> None:
    async with client_for(FakeRunner()) as client:
        agents = await client.get("/hedge-fund/agents")
        response = await client.post("/hedge-fund/run", json=request_body())

    assert len(agents.json()["agents"]) == 19
    assert agents.json()["agents"][0]["key"] == "aswath_damodaran"
    assert agents.json()["agents"][-1]["key"] == "valuation_analyst"
    transcript = events(response)
    assert [item[0] for item in transcript] == ["start", "progress", "complete"]
    assert transcript[0][1] == {"type": "start", "timestamp": None}
    assert transcript[1][1]["agent"] == "valuation_analyst"
    assert transcript[-1][1]["data"]["decisions"]["AAPL"]["action"] == "hold"


async def test_bad_dates_fail_before_streaming_and_child_errors_are_sanitized() -> None:
    invalid = request_body() | {"start_date": "2025-02-01", "end_date": "2025-01-01"}

    class FailingRunner(FakeRunner):
        async def run(self, request: HedgeFundRequest, emit: Any) -> dict[str, object]:
            raise RuntimeError("secret provider payload")

    async with client_for(FailingRunner()) as client:
        bad = await client.post("/hedge-fund/run", json=invalid)
        failed = await client.post("/hedge-fund/run", json=request_body())

    assert bad.status_code == 400
    transcript = events(failed)
    assert [item[0] for item in transcript] == ["start", "error"]
    assert transcript[-1][1]["code"] == "internal_error"
    assert "secret provider payload" not in failed.text
