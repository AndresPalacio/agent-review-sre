from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from fin_graph.domain.market import Price
from fin_graph.strategies.catalog import strategy_catalog


def prices(values: list[float]) -> list[Price]:
    first = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Price(
            time=first + timedelta(days=index),
            open=value,
            high=value + 1,
            low=value - 1,
            close=value,
            adj_close=value,
            volume=1_000,
        )
        for index, value in enumerate(values)
    ]


def positions(signals) -> list[float]:
    return [float(item.metadata["position"]) for item in signals]


def test_sma_crossover_golden_signals_and_order() -> None:
    rows = prices([10, 9, 8, 7, 8, 9, 10, 9, 8, 7])
    strategy = strategy_catalog()["sma_crossover"].create({"fast_window": 2, "slow_window": 5})
    signals = strategy.generate(rows)

    assert positions(signals) == [0, 0, 0, 0, -1, 1, 1, 1, -1, -1]
    assert [item.timestamp for item in signals] == [row.time for row in rows]


def test_momentum_uses_only_prior_and_current_bars() -> None:
    original = prices([100, 101, 103, 99, 96, 100])
    strategy = strategy_catalog()["momentum"].create({"lookback": 2, "threshold": 0.02})
    before = strategy.generate(original[:5])
    after = strategy.generate(original)

    assert before == after[:5]
    assert positions(after) == [0, 0, 1, 0, -1, 0]


def test_rsi_golden_signals_are_finite_and_bounded() -> None:
    values = [100 + math.sin(index / 2) * 10 for index in range(30)]
    strategy = strategy_catalog()["rsi_mean_reversion"].create(
        {"period": 5, "oversold": 40, "overbought": 60}
    )
    result = strategy.generate(prices(values))

    assert all(item.action in {"long", "short", "flat"} for item in result)
    assert all(0 <= item.strength <= 1 for item in result)
    assert any(item.action == "long" for item in result)
    assert any(item.action == "short" for item in result)
