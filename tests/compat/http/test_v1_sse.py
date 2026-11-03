from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from tests.compat.http.test_hedge_fund_routes import events, request_body

from fin_graph.compat.ai_hedge_fund.schemas import BacktestRequest, HedgeFundRequest
from fin_graph.compat.settings import CompatibilityProfile
from fin_graph.server.compat import compatibility_router
from fin_graph.server.compat.events import (
    InternalEvent,
    LegacyDialect,
    legacy_sse,
    stream_legacy_run,
)


class BacktestRunner:
    async def run(self, request: HedgeFundRequest, emit: Any) -> dict[str, object]:
        return {}

    async def backtest(
        self,
        request: BacktestRequest,
        emit: Callable[[InternalEvent], Awaitable[None]],
        cancellation: asyncio.Event,
    ) -> dict[str, object]:
        await emit(
            InternalEvent(
                kind="progress",
                payload={
                    "agent": "backtest",
                    "ticker": None,
                    "status": "Completed 2025-01-02 - Portfolio: $10,100.00",
                    "timestamp": None,
                    "analysis": json.dumps({"date": "2025-01-02", "portfolio_value": 10100}),
                },
            )
        )
        return {
            "performance_metrics": {"sharpe_ratio": 1.2},
            "final_portfolio": {"cash": 10100},
            "total_days": 1,
        }


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


async def test_backtest_sse_transcript_has_exact_wire_order() -> None:
    body = request_body()
    body["initial_capital"] = body.pop("initial_cash")
    async with client_for(BacktestRunner()) as client:
        response = await client.post("/hedge-fund/backtest", json=body)

    transcript = events(response)
    assert [item[0] for item in transcript] == ["start", "progress", "complete"]
    assert transcript[-1][1] == {
        "type": "complete",
        "data": {
            "performance_metrics": {"sharpe_ratio": 1.2},
            "final_portfolio": {"cash": 10100},
            "total_days": 1,
        },
        "timestamp": None,
    }


async def test_event_adapter_emits_only_one_terminal_event() -> None:
    async def source() -> AsyncIterator[InternalEvent]:
        yield InternalEvent(kind="start", payload={})
        yield InternalEvent(kind="error", payload={"message": "failed", "code": "internal_error"})
        yield InternalEvent(kind="complete", payload={"data": {"must": "not appear"}})

    frames = [value async for value in legacy_sse(source(), LegacyDialect.HEDGE_FUND)]
    assert b"event: start" in frames[0]
    assert b"event: error" in frames[1]
    assert len(frames) == 2


async def test_closing_stream_cancels_child_run() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def child(emit: Callable[[InternalEvent], Awaitable[None]]) -> dict[str, object]:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    stream = stream_legacy_run(child, LegacyDialect.HEDGE_FUND)
    first = await anext(stream)
    assert b"event: start" in first
    await started.wait()
    await stream.aclose()
    await asyncio.wait_for(cancelled.wait(), timeout=1)
