"""Point-in-time v2 alpha-model backtesting with reference mechanics."""

from __future__ import annotations

import asyncio
import inspect
import math
import statistics
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fin_graph.backtesting.models import (
    BacktestEvent,
    BacktestEventKind,
    BacktestStatus,
)
from fin_graph.domain.requests.model import unique_tickers
from fin_graph.quant.data import MarketDataClient, PointInTimeData
from fin_graph.quant.signals.base import AlphaModel, JsonValue


class Cancellation(Protocol):
    def is_set(self) -> bool: ...


type EventSink = Callable[[BacktestEvent], Awaitable[None] | None]


class AlphaBacktestRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tickers: tuple[str, ...] = Field(min_length=1)
    start_date: date
    end_date: date
    initial_capital: float = Field(default=100_000, gt=0, allow_inf_nan=False)
    per_trade: float = Field(default=10_000, gt=0, allow_inf_nan=False)
    threshold: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    holding_days: int = Field(default=5, ge=1, le=2_000)

    @field_validator("tickers", mode="before")
    @classmethod
    def normalize_tickers(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list | tuple):
            return tuple(unique_tickers(str(item) for item in value))
        return value

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class AlphaTrade(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    direction: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    shares: float
    pnl: float
    return_pct: float
    holding_days: int
    reasoning: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AlphaPerformanceMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    n_trades: int
    n_long: int
    n_short: int
    avg_return_pct: float
    avg_holding_days: float


class AlphaBacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: AlphaBacktestRequest
    trades: tuple[AlphaTrade, ...] = ()
    metrics: AlphaPerformanceMetrics | None = None
    equity_curve: tuple[float, ...] = ()
    status: BacktestStatus


class _Events:
    def __init__(self, sink: EventSink | None) -> None:
        self.sink = sink
        self.sequence = 0
        self.terminal = False

    async def emit(
        self, kind: BacktestEventKind, progress: float, message: str, day: date | None = None
    ) -> None:
        if kind in {
            BacktestEventKind.COMPLETE,
            BacktestEventKind.CANCELLED,
            BacktestEventKind.ERROR,
        }:
            if self.terminal:
                return
            self.terminal = True
        event = BacktestEvent(
            sequence=self.sequence,
            kind=kind,
            progress=max(0, min(1, progress)),
            message=message,
            date=day,
        )
        self.sequence += 1
        if self.sink is not None:
            outcome = self.sink(event)
            if inspect.isawaitable(outcome):
                await outcome


def _cancelled(cancellation: Cancellation | None) -> bool:
    return cancellation is not None and cancellation.is_set()


def _finish(
    request: AlphaBacktestRequest, trades: list[AlphaTrade], status: BacktestStatus
) -> AlphaBacktestResult:
    if not trades:
        return AlphaBacktestResult(request=request, status=status)
    ordered = sorted(trades, key=lambda item: (item.entry_date, item.ticker))
    equity = request.initial_capital
    curve = [equity]
    for trade in ordered:
        equity += trade.pnl
        curve.append(round(equity, 2))
    returns = [trade.return_pct for trade in ordered]
    total = (curve[-1] - request.initial_capital) / request.initial_capital
    calendar_days = (ordered[-1].exit_date - ordered[0].entry_date).days
    years = max(calendar_days / 365.25, 0.01)
    annualized = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    deviation = statistics.stdev(returns) if len(returns) > 1 else 1.0
    sharpe = (
        statistics.mean(returns) / deviation * math.sqrt(len(returns) / years) if deviation else 0
    )
    peak = curve[0]
    drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak if peak else 0)
    metrics = AlphaPerformanceMetrics(
        total_return_pct=round(total, 6),
        annualized_return_pct=round(annualized, 6),
        sharpe_ratio=round(sharpe, 4),
        max_drawdown_pct=round(drawdown, 6),
        win_rate=round(sum(value > 0 for value in returns) / len(returns), 4),
        n_trades=len(ordered),
        n_long=sum(item.direction == "long" for item in ordered),
        n_short=sum(item.direction == "short" for item in ordered),
        avg_return_pct=round(statistics.mean(returns), 6),
        avg_holding_days=round(statistics.mean(item.holding_days for item in ordered), 1),
    )
    return AlphaBacktestResult(
        request=request,
        trades=tuple(ordered),
        metrics=metrics,
        equity_curve=tuple(curve),
        status=status,
    )


async def run_alpha_backtest(
    request: AlphaBacktestRequest,
    model: AlphaModel,
    data: MarketDataClient,
    *,
    event_sink: EventSink | None,
    cancellation: Cancellation | None,
) -> AlphaBacktestResult:
    """Run the reference edge-trigger/equal-dollar alpha simulation asynchronously."""

    events = _Events(event_sink)
    trades: list[AlphaTrade] = []
    await events.emit(BacktestEventKind.RUNNING, 0, "Backtest started")
    if _cancelled(cancellation):
        await events.emit(BacktestEventKind.CANCELLED, 1, "Backtest cancelled")
        return _finish(request, trades, BacktestStatus.CANCELLED)
    try:
        padded_end = request.end_date + timedelta(days=request.holding_days * 2 + 10)
        histories = {
            ticker: sorted(
                await data.prices(ticker, request.start_date, padded_end),
                key=lambda item: item.time,
            )
            for ticker in request.tickers
        }
        total_days = sum(
            sum(request.start_date <= row.day <= request.end_date for row in rows)
            for rows in histories.values()
        )
        visited = 0
        for ticker in request.tickers:
            rows = histories[ticker]
            by_day = {row.day: row.effective_close for row in rows if row.effective_close > 0}
            all_days = sorted(by_day)
            grid = [day for day in all_days if request.start_date <= day <= request.end_date]
            armed = True
            index = 0
            while index < len(grid):
                day = grid[index]
                if _cancelled(cancellation):
                    await events.emit(BacktestEventKind.CANCELLED, 1, "Backtest cancelled", day)
                    return _finish(request, trades, BacktestStatus.CANCELLED)
                signal = await model.predict(ticker, day, PointInTimeData(data, day))
                visited += 1
                await events.emit(
                    BacktestEventKind.PROGRESS,
                    visited / max(total_days, 1),
                    "Backtest date complete",
                    day,
                )
                if armed and abs(signal.value) > request.threshold:
                    entry_index = all_days.index(day)
                    exit_index = entry_index + request.holding_days
                    if exit_index >= len(all_days):
                        break
                    exit_day = all_days[exit_index]
                    entry_price, exit_price = by_day[day], by_day[exit_day]
                    shares = request.per_trade / entry_price
                    direction = "long" if signal.value > 0 else "short"
                    pnl = shares * (exit_price - entry_price)
                    if direction == "short":
                        pnl = -pnl
                    trades.append(
                        AlphaTrade(
                            ticker=ticker,
                            direction=direction,
                            entry_date=day,
                            exit_date=exit_day,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            shares=round(shares, 4),
                            pnl=round(pnl, 2),
                            return_pct=round(pnl / request.per_trade, 6),
                            holding_days=request.holding_days,
                            reasoning=signal.reasoning,
                            metadata=signal.metadata,
                        )
                    )
                    armed = False
                    index = grid.index(exit_day) if exit_day in grid else len(grid)
                    continue
                if abs(signal.value) <= request.threshold:
                    armed = True
                index += 1
        await events.emit(BacktestEventKind.COMPLETE, 1, "Backtest complete")
        return _finish(request, trades, BacktestStatus.COMPLETED)
    except asyncio.CancelledError:
        await events.emit(BacktestEventKind.CANCELLED, 1, "Backtest cancelled")
        raise
    except Exception as exc:
        await events.emit(BacktestEventKind.ERROR, 1, f"Backtest failed: {type(exc).__name__}")
        raise
