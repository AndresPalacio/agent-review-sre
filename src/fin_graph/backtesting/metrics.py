"""Dependency-free performance metrics matching the reference v1 formulas."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import date
from itertools import pairwise

from fin_graph.backtesting.models import BacktestMetrics, BacktestSnapshot
from fin_graph.domain.market import Price

ANNUAL_TRADING_DAYS = 252
ANNUAL_RISK_FREE_RATE = 0.0434


def benchmark_return_pct(prices: Sequence[Price], start: date, end: date) -> float | None:
    in_range = sorted(
        (bar for bar in prices if start <= bar.day <= end and bar.close > 0),
        key=lambda bar: bar.time,
    )
    if not in_range:
        return None
    return (in_range[-1].close / in_range[0].close - 1) * 100


def calculate_metrics(
    initial_capital: float,
    snapshots: Sequence[BacktestSnapshot],
    benchmark_prices: Sequence[Price],
    exit_pnl: Sequence[float],
    *,
    start_date: date,
    end_date: date,
) -> BacktestMetrics:
    values = [initial_capital, *(snapshot.portfolio_value for snapshot in snapshots)]
    returns = [current / previous - 1 for previous, current in pairwise(values)]
    sharpe: float | None = None
    sortino: float | None = None
    if len(returns) >= 2:
        daily_rf = ANNUAL_RISK_FREE_RATE / ANNUAL_TRADING_DAYS
        excess = [value - daily_rf for value in returns]
        mean_excess = statistics.fmean(excess)
        std_excess = statistics.stdev(excess)
        sharpe = (
            math.sqrt(ANNUAL_TRADING_DAYS) * mean_excess / std_excess if std_excess > 1e-12 else 0.0
        )
        downside = math.sqrt(statistics.fmean(min(value, 0.0) ** 2 for value in excess))
        if downside > 1e-12:
            sortino = math.sqrt(ANNUAL_TRADING_DAYS) * mean_excess / downside
        elif mean_excess <= 0:
            sortino = 0.0

    peak = initial_capital
    max_drawdown = 0.0
    max_drawdown_date: date | None = None
    for snapshot in snapshots:
        peak = max(peak, snapshot.portfolio_value)
        drawdown = (snapshot.portfolio_value - peak) / peak if peak else 0.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_drawdown_date = snapshot.date

    last = snapshots[-1] if snapshots else None
    final_value = last.portfolio_value if last is not None else initial_capital
    return BacktestMetrics(
        total_return_pct=(final_value / initial_capital - 1) * 100,
        benchmark_return_pct=benchmark_return_pct(benchmark_prices, start_date, end_date),
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown_pct=max_drawdown * 100,
        max_drawdown_date=max_drawdown_date,
        win_rate_pct=(sum(value > 0 for value in exit_pnl) / len(exit_pnl) * 100)
        if exit_pnl
        else None,
        long_short_ratio=last.long_short_ratio if last is not None else None,
        gross_exposure=last.gross_exposure if last is not None else 0,
        net_exposure=last.net_exposure if last is not None else 0,
    )
