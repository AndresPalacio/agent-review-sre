"""Portfolio backtester for code-defined strategies with next-bar execution."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fin_graph.backtesting.models import BacktestEventKind, BacktestStatus
from fin_graph.backtesting.quant import Cancellation, EventSink, _cancelled, _Events
from fin_graph.domain.market import Price
from fin_graph.domain.requests.model import unique_tickers
from fin_graph.quant.data import MarketDataClient
from fin_graph.strategies.base import AlgorithmStrategy
from fin_graph.strategies.catalog import ParameterValue

_PARAMETER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class StrategyBacktestRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    algorithm_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    parameters: dict[str, ParameterValue] = Field(default_factory=dict, max_length=32)
    symbols: tuple[str, ...] = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    interval: str = "1D"
    provider: str = "yahoo"
    initial_capital: float = Field(default=100_000, gt=0, allow_inf_nan=False)
    commission_rate: float = Field(default=0.001, ge=0, le=0.1, allow_inf_nan=False)
    slippage_bps: float = Field(default=5, ge=0, le=1_000, allow_inf_nan=False)
    seed: int = 42

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list | tuple):
            return tuple(unique_tickers(str(item) for item in value))
        return value

    @field_validator("parameters")
    @classmethod
    def scalar_parameters(cls, value: dict[str, ParameterValue]) -> dict[str, ParameterValue]:
        for name, item in value.items():
            if not _PARAMETER_NAME.fullmatch(name):
                raise ValueError("invalid algorithm parameter name")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError(f"parameter {name!r} must be finite")
        return value

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class StrategyTrade(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    side: Literal["long", "short"]
    entry_date: date
    entry_price: float
    quantity: float
    exit_date: date
    exit_price: float
    pnl: float = 0
    return_pct: float = 0
    commission: float = 0
    reason: str = "rebalance"


class EquityPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    date: date
    equity: float
    cash: float
    long_exposure: float = 0
    short_exposure: float = 0
    gross_exposure: float = 0
    net_exposure: float = 0
    weights: dict[str, float] = Field(default_factory=dict)


class StrategyBacktestMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total_return: float = 0
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown: float = 0
    win_rate: float | None = None
    trade_count: int = 0
    gross_exposure: float = 0
    net_exposure: float = 0


class StrategySignalPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    date: date
    symbol: str
    raw_signal: float = Field(ge=-1, le=1)
    applied_weight: float = Field(ge=-1, le=1)


class StrategySignalSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    first_date: date
    last_date: date
    latest_raw_signal: float
    latest_applied_weight: float
    long_count: int = Field(ge=0)
    short_count: int = Field(ge=0)
    flat_count: int = Field(ge=0)


class StrategyDataCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    first_date: date
    last_date: date
    row_count: int = Field(ge=0)


class StrategyBacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: StrategyBacktestRequest
    status: BacktestStatus
    metrics: StrategyBacktestMetrics
    equity_curve: tuple[EquityPoint, ...] = ()
    trades: tuple[StrategyTrade, ...] = ()
    signal_summary: tuple[StrategySignalSummary, ...] = ()
    data_coverage: dict[str, StrategyDataCoverage] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()


class StrategyBacktestOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: StrategyBacktestResult
    signals: tuple[StrategySignalPoint, ...]


@dataclass
class _Position:
    quantity: float
    average_entry: float
    entry_date: date
    entry_commission: float


def _signal_value(action: str, strength: float, metadata: Mapping[str, object]) -> float:
    raw = metadata.get("position")
    if isinstance(raw, int | float) and not isinstance(raw, bool) and math.isfinite(float(raw)):
        return max(-1.0, min(1.0, float(raw)))
    return strength if action == "long" else -strength if action == "short" else 0.0


def _metrics(
    curve: list[EquityPoint], trades: list[StrategyTrade], initial: float
) -> StrategyBacktestMetrics:
    if not curve:
        return StrategyBacktestMetrics()
    values = [point.equity for point in curve]
    total = values[-1] / initial - 1
    returns = [
        values[index] / values[index - 1] - 1
        for index in range(1, len(values))
        if values[index - 1]
    ]
    periods = len(returns)
    annualized = (
        (values[-1] / initial) ** (252 / periods) - 1 if periods and values[-1] > 0 else None
    )
    mean = sum(returns) / len(returns) if returns else 0
    variance = sum((item - mean) ** 2 for item in returns) / len(returns) if returns else 0
    volatility = math.sqrt(variance)
    downside = [item for item in returns if item < 0]
    downside_deviation = (
        math.sqrt(sum(item * item for item in downside) / len(downside)) if downside else 0
    )
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak if peak else 0)
    return StrategyBacktestMetrics(
        total_return=round(total, 12),
        annualized_return=annualized,
        annualized_volatility=volatility * math.sqrt(252) if returns else None,
        sharpe_ratio=mean / volatility * math.sqrt(252) if volatility else None,
        sortino_ratio=mean / downside_deviation * math.sqrt(252) if downside_deviation else None,
        max_drawdown=drawdown,
        win_rate=sum(item.pnl > 0 for item in trades) / len(trades) if trades else None,
        trade_count=len(trades),
        gross_exposure=sum(item.gross_exposure for item in curve) / len(curve),
        net_exposure=sum(item.net_exposure for item in curve) / len(curve),
    )


def _slipped(price: float, order_quantity: float, bps: float) -> float:
    side = 1 if order_quantity > 0 else -1
    return price * (1 + side * bps / 10_000)


def _close(
    *,
    symbol: str,
    quantity: float,
    price: float,
    day: date,
    reason: str,
    cash: float,
    positions: dict[str, _Position],
    trades: list[StrategyTrade],
    request: StrategyBacktestRequest,
) -> float:
    position = positions[symbol]
    available = abs(position.quantity)
    close_size = min(quantity, available)
    direction = 1.0 if position.quantity > 0 else -1.0
    order_quantity = -direction * close_size
    execution = _slipped(price, order_quantity, request.slippage_bps)
    exit_commission = abs(order_quantity * execution) * request.commission_rate
    entry_commission = position.entry_commission * close_size / available
    gross_pnl = direction * close_size * (execution - position.average_entry)
    net_pnl = gross_pnl - entry_commission - exit_commission
    notional = close_size * position.average_entry
    trades.append(
        StrategyTrade(
            symbol=symbol,
            side="long" if direction > 0 else "short",
            entry_date=position.entry_date,
            entry_price=position.average_entry,
            quantity=close_size,
            exit_date=day,
            exit_price=execution,
            pnl=net_pnl,
            return_pct=net_pnl / notional if notional else 0,
            commission=entry_commission + exit_commission,
            reason=reason,
        )
    )
    cash = cash - order_quantity * execution - exit_commission
    remaining = available - close_size
    if remaining <= 1e-10:
        del positions[symbol]
    else:
        position.quantity = direction * remaining
        position.entry_commission -= entry_commission
    return cash


def _rebalance(
    *,
    symbol: str,
    target: float,
    price: float,
    day: date,
    cash: float,
    positions: dict[str, _Position],
    trades: list[StrategyTrade],
    request: StrategyBacktestRequest,
) -> float:
    current = positions.get(symbol)
    current_quantity = current.quantity if current else 0
    if abs(target - current_quantity) <= 1e-10:
        return cash
    if current and current_quantity * target <= 0:
        cash = _close(
            symbol=symbol,
            quantity=abs(current_quantity),
            price=price,
            day=day,
            reason="signal",
            cash=cash,
            positions=positions,
            trades=trades,
            request=request,
        )
        current = None
        current_quantity = 0
    if abs(target) <= 1e-10:
        return cash
    if current and current_quantity * target > 0:
        if abs(target) < abs(current_quantity):
            return _close(
                symbol=symbol,
                quantity=abs(current_quantity) - abs(target),
                price=price,
                day=day,
                reason="rebalance",
                cash=cash,
                positions=positions,
                trades=trades,
                request=request,
            )
        order = target - current_quantity
        execution = _slipped(price, order, request.slippage_bps)
        commission = abs(order * execution) * request.commission_rate
        old, added = abs(current_quantity), abs(order)
        current.average_entry = (current.average_entry * old + execution * added) / (old + added)
        current.quantity = target
        current.entry_commission += commission
        return cash - order * execution - commission
    execution = _slipped(price, target, request.slippage_bps)
    commission = abs(target * execution) * request.commission_rate
    positions[symbol] = _Position(target, execution, day, commission)
    return cash - target * execution - commission


def _partial_result(
    request: StrategyBacktestRequest,
    status: BacktestStatus,
    curve: list[EquityPoint],
    trades: list[StrategyTrade],
    signals: list[StrategySignalPoint],
    histories: dict[str, list[Price]],
) -> StrategyBacktestOutput:
    summaries: list[StrategySignalSummary] = []
    coverage: dict[str, StrategyDataCoverage] = {}
    for symbol, rows in histories.items():
        relevant = [item for item in signals if item.symbol == symbol]
        if rows:
            coverage[symbol] = StrategyDataCoverage(
                first_date=rows[0].day, last_date=rows[-1].day, row_count=len(rows)
            )
        if relevant:
            summaries.append(
                StrategySignalSummary(
                    symbol=symbol,
                    first_date=relevant[0].date,
                    last_date=relevant[-1].date,
                    latest_raw_signal=relevant[-1].raw_signal,
                    latest_applied_weight=relevant[-1].applied_weight,
                    long_count=sum(item.raw_signal > 1e-12 for item in relevant),
                    short_count=sum(item.raw_signal < -1e-12 for item in relevant),
                    flat_count=sum(abs(item.raw_signal) <= 1e-12 for item in relevant),
                )
            )
    warnings = []
    if signals and all(abs(item.raw_signal) <= 1e-12 for item in signals):
        warnings.append("all-signals-flat")
    if not trades:
        warnings.append("no-trades")
    result = StrategyBacktestResult(
        request=request,
        status=status,
        metrics=_metrics(curve, trades, request.initial_capital),
        equity_curve=tuple(curve),
        trades=tuple(trades),
        signal_summary=tuple(summaries),
        data_coverage=coverage,
        warnings=tuple(warnings),
    )
    return StrategyBacktestOutput(result=result, signals=tuple(signals))


async def run_strategy_backtest(
    request: StrategyBacktestRequest,
    strategy: AlgorithmStrategy,
    data: MarketDataClient,
    *,
    event_sink: EventSink | None,
    cancellation: Cancellation | None,
    data_concurrency: int = 8,
) -> StrategyBacktestOutput:
    """Generate pure signals, shift them one bar, then rebalance one global account."""

    if data_concurrency < 1:
        raise ValueError("data_concurrency must be at least one")
    events = _Events(event_sink)
    await events.emit(BacktestEventKind.RUNNING, 0, "Backtest started")
    histories: dict[str, list[Price]] = {}
    curve: list[EquityPoint] = []
    trades: list[StrategyTrade] = []
    signal_points: list[StrategySignalPoint] = []
    try:
        semaphore = asyncio.Semaphore(data_concurrency)

        async def load_history(symbol: str) -> list[Price]:
            async with semaphore:
                rows = sorted(
                    await data.prices(symbol, request.start_date, request.end_date),
                    key=lambda item: item.time,
                )
            if not rows:
                raise ValueError(f"Missing market data for {symbol}")
            return rows

        loads: dict[str, asyncio.Task[list[Price]]] = {}
        try:
            async with asyncio.TaskGroup() as group:
                loads = {
                    symbol: group.create_task(load_history(symbol)) for symbol in request.symbols
                }
        except ExceptionGroup as group_error:
            for symbol in request.symbols:
                task = loads.get(symbol)
                if task is None or task.cancelled() or not task.done():
                    continue
                if error := task.exception():
                    raise error from None
            raise group_error
        histories.update((symbol, loads[symbol].result()) for symbol in request.symbols)
        raw: dict[str, dict[date, float]] = {}
        bars: dict[str, dict[date, Price]] = {}
        for symbol, rows in histories.items():
            generated = tuple(strategy.generate(rows, request.parameters))
            if len(generated) != len(rows):
                raise ValueError(f"strategy output length does not match {symbol} price history")
            raw[symbol] = {
                row.day: _signal_value(signal.action, signal.strength, signal.metadata)
                for row, signal in zip(rows, generated, strict=True)
            }
            bars[symbol] = {row.day: row for row in rows}
        days = sorted({day for values in bars.values() for day in values})
        local_targets: dict[str, dict[date, float]] = {}
        for symbol in request.symbols:
            prior = 0.0
            local_targets[symbol] = {}
            for day in sorted(raw[symbol]):
                local_targets[symbol][day] = prior
                prior = raw[symbol][day]
        targets: dict[date, dict[str, float]] = {}
        last_target = {symbol: 0.0 for symbol in request.symbols}
        missing_bars = {symbol: 0 for symbol in request.symbols}
        for day in days:
            weights: dict[str, float] = {}
            for symbol in request.symbols:
                if day in local_targets[symbol]:
                    last_target[symbol] = local_targets[symbol][day]
                    missing_bars[symbol] = 0
                else:
                    missing_bars[symbol] += 1
                weights[symbol] = last_target[symbol] if missing_bars[symbol] <= 5 else 0.0
            scale = max(1.0, sum(abs(value) for value in weights.values()))
            targets[day] = {symbol: value / scale for symbol, value in weights.items()}
            signal_points.extend(
                StrategySignalPoint(
                    date=day,
                    symbol=symbol,
                    raw_signal=raw[symbol].get(day, 0),
                    applied_weight=targets[day][symbol],
                )
                for symbol in request.symbols
            )
        cash = float(request.initial_capital)
        positions: dict[str, _Position] = {}
        last_close: dict[str, float] = {}
        for index, day in enumerate(days):
            if _cancelled(cancellation):
                await events.emit(BacktestEventKind.CANCELLED, 1, "Backtest cancelled", day)
                return _partial_result(
                    request, BacktestStatus.CANCELLED, curve, trades, signal_points, histories
                )
            open_marks = {
                symbol: bars[symbol][day].open
                if day in bars[symbol]
                else last_close.get(symbol, position.average_entry)
                for symbol, position in positions.items()
            }
            equity_at_open = cash + sum(
                position.quantity * open_marks[symbol] for symbol, position in positions.items()
            )
            for symbol in request.symbols:
                if day not in bars[symbol]:
                    continue
                price = bars[symbol][day].open
                if price <= 0:
                    raise ValueError(f"Invalid open price for {symbol} on {day}")
                target = targets[day][symbol] * equity_at_open / price
                cash = _rebalance(
                    symbol=symbol,
                    target=target,
                    price=price,
                    day=day,
                    cash=cash,
                    positions=positions,
                    trades=trades,
                    request=request,
                )
            for symbol in request.symbols:
                if day in bars[symbol]:
                    last_close[symbol] = bars[symbol][day].effective_close
            final_bar = index == len(days) - 1
            if final_bar:
                for symbol in list(positions):
                    cash = _close(
                        symbol=symbol,
                        quantity=abs(positions[symbol].quantity),
                        price=last_close[symbol],
                        day=day,
                        reason="end_of_backtest",
                        cash=cash,
                        positions=positions,
                        trades=trades,
                        request=request,
                    )
            equity = cash + sum(
                position.quantity * last_close.get(symbol, position.average_entry)
                for symbol, position in positions.items()
            )
            long_notional = sum(
                max(position.quantity * last_close.get(symbol, position.average_entry), 0)
                for symbol, position in positions.items()
            )
            short_notional = sum(
                abs(min(position.quantity * last_close.get(symbol, position.average_entry), 0))
                for symbol, position in positions.items()
            )
            denominator = equity if abs(equity) > 1e-12 else 1
            curve.append(
                EquityPoint(
                    date=day,
                    equity=equity,
                    cash=cash,
                    long_exposure=long_notional / denominator,
                    short_exposure=short_notional / denominator,
                    gross_exposure=(long_notional + short_notional) / denominator,
                    net_exposure=(long_notional - short_notional) / denominator,
                    weights={}
                    if final_bar
                    else {
                        symbol: value
                        for symbol, value in targets[day].items()
                        if abs(value) > 1e-12
                    },
                )
            )
            await events.emit(
                BacktestEventKind.PROGRESS,
                (index + 1) / len(days),
                "Backtest date complete",
                day,
            )
        await events.emit(BacktestEventKind.COMPLETE, 1, "Backtest complete")
        return _partial_result(
            request, BacktestStatus.COMPLETED, curve, trades, signal_points, histories
        )
    except asyncio.CancelledError:
        await events.emit(BacktestEventKind.CANCELLED, 1, "Backtest cancelled")
        raise
    except Exception as exc:
        await events.emit(BacktestEventKind.ERROR, 1, f"Backtest failed: {type(exc).__name__}")
        raise


# Original v2 public names remain import-compatible.
AlgorithmBacktestConfig = StrategyBacktestRequest
AlgorithmBacktestResult = StrategyBacktestResult
AlgorithmSignalPoint = StrategySignalPoint
