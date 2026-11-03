from __future__ import annotations

import math
from datetime import date

from fin_graph.backtesting.metrics import calculate_metrics
from fin_graph.backtesting.models import BacktestSnapshot
from fin_graph.domain.portfolio import Portfolio


def _snapshot(day: int, value: float) -> BacktestSnapshot:
    return BacktestSnapshot(
        date=date(2025, 1, day),
        portfolio=Portfolio(cash=value),
        portfolio_value=value,
        decisions={},
        executed_trades={},
        realized_pnl={},
        analyst_signals={},
        current_prices={},
    )


def test_sortino_is_positive_infinity_when_all_excess_returns_are_positive() -> None:
    metrics = calculate_metrics(
        100.0,
        [_snapshot(2, 101.0), _snapshot(3, 102.0)],
        (),
        (),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 3),
    )

    assert metrics.sortino_ratio is not None
    assert math.isinf(metrics.sortino_ratio) and metrics.sortino_ratio > 0
