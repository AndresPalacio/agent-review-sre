"""Drop-in hedge-fund analysis, backtest, and analyst-catalog streams."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from fin_graph.backtesting import BacktestEventKind, run_v1_backtest
from fin_graph.compat.ai_hedge_fund.adapters import (
    CompatibilityAdapterError,
    from_native_backtest_result,
    to_native_analysis_request,
    to_native_backtest_request,
    to_native_portfolio,
)
from fin_graph.compat.ai_hedge_fund.models import LegacyHedgeFundResult
from fin_graph.compat.ai_hedge_fund.schemas import BacktestRequest, HedgeFundRequest
from fin_graph.domain.analysts import analyst_catalog
from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.requests.model import AnalysisRequest
from fin_graph.graphs.analysis.snapshots import MarketSnapshotServices
from fin_graph.integrations.context import RunContext
from fin_graph.server.compat.events import (
    EventEmitter,
    InternalEvent,
    LegacyDialect,
    stream_legacy_run,
)

router = APIRouter(prefix="/hedge-fund")


class LegacyRunLimiter:
    """Event-loop-local admission control acquired before response headers are sent."""

    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        self.limit = limit
        self.active = 0

    def try_acquire(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)


class GraphHedgeFundService:
    """Thin bridge to the compatibility graph and injected point-in-time backtester."""

    def __init__(self, analysis_graph: Any, backtest_runner: Any = None) -> None:
        self.analysis_graph = analysis_graph
        self.backtest_runner = backtest_runner

    async def run(self, request: HedgeFundRequest, emit: EventEmitter) -> dict[str, Any]:
        native = to_native_analysis_request(request)
        await emit(
            InternalEvent(
                kind="progress",
                payload={
                    "agent": "system",
                    "ticker": None,
                    "status": "Preparing hedge fund run",
                    "timestamp": None,
                    "analysis": None,
                },
            )
        )
        result = await self.analysis_graph.ainvoke(
            {
                "request": native.model_dump(mode="json"),
                "portfolio": (
                    native.portfolio.model_dump(mode="json")
                    if native.portfolio is not None
                    else None
                ),
            }
        )
        value = result.get("result") if isinstance(result, dict) else None
        if not isinstance(value, dict):
            raise RuntimeError("analysis did not produce a compatible result")
        return value

    async def backtest(
        self,
        request: BacktestRequest,
        emit: EventEmitter,
        cancellation: asyncio.Event,
    ) -> dict[str, Any]:
        if self.backtest_runner is None:
            raise RuntimeError("backtest service unavailable")
        value = self.backtest_runner(request, emit, cancellation)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, dict):
            raise RuntimeError("backtest did not produce a compatible result")
        return value


class _GraphAnalysisRunner:
    def __init__(self, graph: Any) -> None:
        self.graph = graph

    async def __call__(
        self,
        request: AnalysisRequest,
        portfolio: Portfolio,
        data: object,
        *,
        model: str,
    ) -> LegacyHedgeFundResult:
        del data, model
        state = await self.graph.ainvoke(
            {
                "request": request.model_dump(mode="json"),
                "portfolio": portfolio.model_dump(mode="json"),
            }
        )
        if not isinstance(state, dict) or not isinstance(state.get("result"), dict):
            raise RuntimeError("analysis did not produce a compatible result")
        return LegacyHedgeFundResult.model_validate(state["result"])


class _BacktestData:
    def __init__(self, context: RunContext) -> None:
        self.context = context
        self.snapshots = MarketSnapshotServices()

    async def price_history(self, ticker: str, start_date: date, end_date: date) -> list[Any]:
        from fin_graph.integrations.analytics.engine import AnalyticsEngine
        from fin_graph.integrations.market_data import MarketDataClient

        engine = self.context.client("analytics", AnalyticsEngine)
        client = self.context.clients.get(engine.data_integration)
        if not isinstance(client, MarketDataClient):
            raise TypeError(f"{engine.data_integration} is not a market-data client")
        return await client.prices(ticker, start_date, end_date)

    async def company_snapshot(
        self, ticker: str, start_date: date, end_date: date
    ) -> CompanySnapshot:
        del start_date
        return await self.snapshots.company_snapshot(ticker, end_date, self.context)


def make_v1_backtest_runner(
    graph: Any, context_factory: Callable[[], RunContext]
) -> Callable[[BacktestRequest, EventEmitter, asyncio.Event], Awaitable[dict[str, Any]]]:
    """Adapt the native point-in-time backtester to the old streaming completion shape."""

    async def run(
        request: BacktestRequest,
        emit: EventEmitter,
        cancellation: asyncio.Event,
    ) -> dict[str, Any]:
        native = to_native_backtest_request(request)

        async def on_event(event: Any) -> None:
            if event.kind not in {BacktestEventKind.RUNNING, BacktestEventKind.PROGRESS}:
                return
            await emit(
                InternalEvent(
                    kind="progress",
                    payload={
                        "agent": "backtest",
                        "ticker": None,
                        "status": event.message,
                        "timestamp": None,
                        "analysis": None,
                    },
                )
            )

        result = await run_v1_backtest(
            native,
            _GraphAnalysisRunner(graph),
            _BacktestData(context_factory()),
            event_sink=on_event,
            cancellation=cancellation,
            initial_portfolio=to_native_portfolio(request, cash=request.initial_capital),
        )
        compatible = from_native_backtest_result(result)
        for day in compatible.results:
            await emit(
                InternalEvent(
                    kind="progress",
                    payload={
                        "agent": "backtest",
                        "ticker": None,
                        "status": (
                            f"Completed {day.date} - Portfolio: ${day.portfolio_value:,.2f}"
                        ),
                        "timestamp": None,
                        "analysis": json.dumps(day.model_dump(mode="json"), separators=(",", ":")),
                    },
                )
            )
        return {
            "performance_metrics": compatible.performance_metrics.model_dump(mode="json"),
            "final_portfolio": compatible.final_portfolio,
            "total_days": len(compatible.results),
        }

    return run


def _runner(request: Request) -> Any:
    runner = getattr(request.app.state, "compatibility_hedge_fund", None)
    if runner is not None:
        return runner
    graph = getattr(request.app.state, "compatibility_analysis_graph", None)
    if graph is None:
        raise HTTPException(503, "hedge fund service unavailable")
    runner = GraphHedgeFundService(
        graph, getattr(request.app.state, "compatibility_backtest_runner", None)
    )
    request.app.state.compatibility_hedge_fund = runner
    return runner


def _limiter(request: Request) -> LegacyRunLimiter:
    limiter = getattr(request.app.state, "compatibility_run_limiter", None)
    if not isinstance(limiter, LegacyRunLimiter):
        limiter = LegacyRunLimiter(8)
        request.app.state.compatibility_run_limiter = limiter
    return limiter


def _stream(
    request: Request,
    child: Callable[[EventEmitter], Awaitable[dict[str, Any]]],
    dialect: LegacyDialect,
) -> StreamingResponse:
    limiter = _limiter(request)
    if not limiter.try_acquire():
        raise HTTPException(
            429,
            "run capacity exhausted",
            headers={"Retry-After": "1"},
        )

    async def release_after() -> AsyncIterator[bytes]:
        try:
            async for frame in stream_legacy_run(child, dialect):
                yield frame
        finally:
            limiter.release()

    return StreamingResponse(
        release_after(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/run")
async def run(body: HedgeFundRequest, request: Request) -> StreamingResponse:
    try:
        to_native_analysis_request(body)
    except CompatibilityAdapterError as exc:
        raise HTTPException(400, str(exc)) from exc
    runner = _runner(request)

    async def child(emit: EventEmitter) -> dict[str, Any]:
        return dict(await runner.run(body, emit))

    return _stream(request, child, LegacyDialect.HEDGE_FUND)


@router.post("/backtest")
async def backtest(body: BacktestRequest, request: Request) -> StreamingResponse:
    try:
        to_native_backtest_request(body)
    except CompatibilityAdapterError as exc:
        raise HTTPException(400, str(exc)) from exc
    runner = _runner(request)
    cancellation = asyncio.Event()

    async def child(emit: EventEmitter) -> dict[str, Any]:
        try:
            return dict(await runner.backtest(body, emit, cancellation))
        finally:
            cancellation.set()

    return _stream(request, child, LegacyDialect.BACKTEST)


@router.get("/agents")
async def agents() -> dict[str, list[dict[str, Any]]]:
    return {
        "agents": [
            {
                "key": item.id.value,
                "display_name": item.display_name,
                "description": item.description,
                "investing_style": item.investing_style,
                "order": item.order,
            }
            for item in analyst_catalog()
        ]
    }
