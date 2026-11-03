"""Point-in-time v1 backtest loop with injected analysis, data, events, and cancellation."""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from fin_graph.backtesting.engine import execute_day
from fin_graph.backtesting.metrics import benchmark_return_pct, calculate_metrics
from fin_graph.backtesting.models import (
    BacktestEvent,
    BacktestEventKind,
    BacktestRequest,
    BacktestResult,
    BacktestSnapshot,
    BacktestStatus,
)
from fin_graph.compat.ai_hedge_fund.models import LegacyHedgeFundResult
from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import Price
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.requests.model import AnalysisRequest, RequestSource


class BacktestDataClient(Protocol):
    async def price_history(
        self, ticker: str, start_date: date, end_date: date
    ) -> Sequence[Price]: ...

    async def company_snapshot(
        self, ticker: str, start_date: date, end_date: date
    ) -> CompanySnapshot: ...


class AnalysisRunner(Protocol):
    async def __call__(
        self,
        request: AnalysisRequest,
        portfolio: Portfolio,
        data: PointInTimeDataView,
        *,
        model: str,
    ) -> LegacyHedgeFundResult: ...


class Cancellation(Protocol):
    def is_set(self) -> bool: ...


type EventSink = Callable[[BacktestEvent], Awaitable[None] | None]


async def _gather_cancel_on_error[ValueT](
    awaitables: Sequence[Awaitable[ValueT]],
) -> list[ValueT]:
    """Gather results without leaving sibling I/O running after one operation fails."""

    tasks: list[asyncio.Future[ValueT]] = [
        asyncio.ensure_future(awaitable) for awaitable in awaitables
    ]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


@dataclass(frozen=True)
class PointInTimeDataView:
    """A dated view that never exposes observations later than ``as_of``."""

    as_of: date
    _histories: Mapping[str, Sequence[Price]]
    _snapshots: Mapping[str, CompanySnapshot]

    def price_history(self, ticker: str) -> tuple[Price, ...]:
        return tuple(
            sorted(
                (bar for bar in self._histories.get(ticker, ()) if bar.day <= self.as_of),
                key=lambda bar: bar.time,
            )
        )

    def company_snapshot(self, ticker: str) -> CompanySnapshot:
        source = self._snapshots[ticker]
        metrics = tuple(item for item in source.metrics if item.available_on <= self.as_of)
        line_items = tuple(item for item in source.line_items if item.available_on <= self.as_of)
        news = tuple(item for item in source.news if item.date.date() <= self.as_of)
        insider_trades = tuple(
            item for item in source.insider_trades if item.filing_date <= self.as_of
        )
        market_cap = source.market_cap if source.as_of <= self.as_of else None
        if market_cap is None:
            market_cap = next(
                (
                    item.market_cap
                    for item in sorted(metrics, key=lambda value: value.report_period, reverse=True)
                    if item.market_cap
                ),
                None,
            )
        return CompanySnapshot(
            ticker=ticker,
            as_of=self.as_of,
            prices=self.price_history(ticker),
            metrics=metrics,
            line_items=line_items,
            news=news,
            insider_trades=insider_trades,
            market_cap=market_cap,
        )


def _business_days(start: date, end: date) -> tuple[date, ...]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return tuple(days)


def _prices_on(
    tickers: Sequence[str], histories: Mapping[str, Sequence[Price]], day: date
) -> dict[str, float] | None:
    prices: dict[str, float] = {}
    for ticker in tickers:
        bars = [bar for bar in histories[ticker] if bar.day == day]
        if not bars:
            return None
        price = sorted(bars, key=lambda bar: bar.time)[-1].close
        if not math.isfinite(price) or price <= 0:
            return None
        prices[ticker] = price
    return prices


async def run_v1_backtest(
    request: BacktestRequest,
    analysis_runner: AnalysisRunner,
    data_client: BacktestDataClient,
    *,
    event_sink: EventSink | None,
    cancellation: Cancellation | None,
    initial_portfolio: Portfolio | None = None,
) -> BacktestResult:
    """Run every business date, cap all data at that date, and emit one terminal event."""

    sequence = 0
    terminal_emitted = False

    async def emit(
        kind: BacktestEventKind,
        progress: float,
        message: str,
        day: date | None = None,
    ) -> None:
        nonlocal sequence, terminal_emitted
        if kind in {
            BacktestEventKind.COMPLETE,
            BacktestEventKind.CANCELLED,
            BacktestEventKind.ERROR,
        }:
            if terminal_emitted:
                return
            terminal_emitted = True
        event = BacktestEvent(
            sequence=sequence,
            kind=kind,
            progress=progress,
            message=message,
            date=day,
        )
        sequence += 1
        if event_sink is not None:
            emitted = event_sink(event)
            if inspect.isawaitable(emitted):
                await emitted

    def cancelled() -> bool:
        return cancellation is not None and cancellation.is_set()

    portfolio = initial_portfolio or Portfolio(
        cash=request.initial_capital,
        margin_requirement=request.margin_requirement,
    )
    snapshots: list[BacktestSnapshot] = []
    exit_pnl: list[float] = []
    histories: dict[str, Sequence[Price]] = {}
    benchmark: Sequence[Price] = ()

    async def finish(status: BacktestStatus) -> BacktestResult:
        metric_end = (
            snapshots[-1].date
            if status is BacktestStatus.CANCELLED and snapshots
            else request.end_date
        )
        metric_benchmark = () if status is BacktestStatus.CANCELLED and not snapshots else benchmark
        metrics = calculate_metrics(
            request.initial_capital,
            snapshots,
            metric_benchmark,
            exit_pnl,
            start_date=request.start_date,
            end_date=metric_end,
        )
        return BacktestResult(
            request=request,
            snapshots=tuple(snapshots),
            metrics=metrics,
            final_portfolio=portfolio,
            status=status,
        )

    await emit(BacktestEventKind.RUNNING, 0, "Backtest started")
    if cancelled():
        await emit(BacktestEventKind.CANCELLED, 1, "Backtest cancelled")
        return await finish(BacktestStatus.CANCELLED)

    try:
        history_start = request.start_date - timedelta(days=366)

        async def load_history(ticker: str) -> tuple[str, Sequence[Price]]:
            values = await data_client.price_history(ticker, history_start, request.end_date)
            return ticker, tuple(values)

        loaded = await _gather_cancel_on_error(
            [load_history(ticker) for ticker in (*request.tickers, "SPY")]
        )
        histories = dict(loaded)
        benchmark = histories["SPY"]

        async def load_snapshot(ticker: str) -> tuple[str, CompanySnapshot]:
            value = await data_client.company_snapshot(ticker, history_start, request.end_date)
            return ticker, value

        full_snapshots = dict(
            await _gather_cancel_on_error([load_snapshot(ticker) for ticker in request.tickers])
        )
        days = _business_days(request.start_date, request.end_date)
        for index, day in enumerate(days, start=1):
            if cancelled():
                await emit(BacktestEventKind.CANCELLED, 1, "Backtest cancelled", day)
                return await finish(BacktestStatus.CANCELLED)
            current_prices = _prices_on(request.tickers, histories, day)
            progress = index / len(days) if days else 1
            if current_prices is None:
                await emit(
                    BacktestEventKind.PROGRESS,
                    progress,
                    "Skipped date with missing prices",
                    day,
                )
                continue
            view = PointInTimeDataView(day, histories, full_snapshots)
            analysis_request = AnalysisRequest(
                tickers=list(request.tickers),
                as_of=day,
                source=RequestSource.SCHEDULE,
                portfolio=portfolio,
                analysts=request.analysts,
            )
            analysis = LegacyHedgeFundResult.model_validate(
                await analysis_runner(analysis_request, portfolio, view, model=request.model)
            )
            execution = execute_day(portfolio, request.tickers, current_prices, analysis)
            portfolio = execution.portfolio
            exit_pnl.extend(execution.exit_pnl)
            snapshot = BacktestSnapshot(
                date=day,
                portfolio=portfolio,
                portfolio_value=execution.portfolio_value,
                decisions=analysis.decisions,
                executed_trades=execution.executed_trades,
                realized_pnl=execution.realized_pnl,
                analyst_signals=analysis.analyst_signals,
                current_prices=current_prices,
                benchmark_return_pct=benchmark_return_pct(benchmark, request.start_date, day),
                long_exposure=execution.long_exposure,
                short_exposure=execution.short_exposure,
                gross_exposure=execution.gross_exposure,
                net_exposure=execution.net_exposure,
                long_short_ratio=execution.long_short_ratio,
            )
            snapshots.append(snapshot)
            await emit(BacktestEventKind.PROGRESS, progress, "Backtest date complete", day)

        await emit(BacktestEventKind.COMPLETE, 1, "Backtest complete")
        return await finish(BacktestStatus.COMPLETED)
    except asyncio.CancelledError:
        await emit(BacktestEventKind.CANCELLED, 1, "Backtest cancelled")
        raise
    except Exception as exc:
        await emit(BacktestEventKind.ERROR, 1, f"Backtest failed: {type(exc).__name__}")
        raise
