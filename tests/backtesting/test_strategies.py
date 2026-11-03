from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import ClassVar

from fin_graph.backtesting.models import BacktestEventKind, BacktestStatus
from fin_graph.backtesting.strategies import StrategyBacktestRequest, run_strategy_backtest
from fin_graph.domain.market import Price
from fin_graph.strategies.base import AlgorithmSignal, AlgorithmStrategy
from fin_graph.strategies.catalog import AlgorithmSpecification


def _prices(values: list[float]) -> list[Price]:
    first = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Price(
            time=first + timedelta(days=index),
            open=value,
            high=value,
            low=value,
            close=value,
            adj_close=value,
            volume=1_000,
        )
        for index, value in enumerate(values)
    ]


class PriceData:
    def __init__(self, values: list[Price]) -> None:
        self.values = values

    async def prices(self, ticker: str, start: date, end: date) -> list[Price]:
        return [row for row in self.values if start <= row.day <= end]


class StepStrategy(AlgorithmStrategy):
    specification: ClassVar[AlgorithmSpecification] = AlgorithmSpecification(
        key="step_strategy",
        name="Step strategy",
        description="Known deterministic positions for engine tests.",
        category="test",
        version="1.0.0",
    )

    def generate(
        self,
        data: Sequence[Price],
        parameters: Mapping[str, object] | None = None,
    ) -> Sequence[AlgorithmSignal]:
        return self._signals(data, [1.0, 1.0, 0.0, 0.0])


async def test_strategy_uses_next_bar_and_matches_reference_portfolio() -> None:
    result = await run_strategy_backtest(
        StrategyBacktestRequest(
            algorithm_key="step_strategy",
            symbols=("AAA",),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 4),
            initial_capital=100_000,
            commission_rate=0,
            slippage_bps=0,
        ),
        StepStrategy(ticker="AAA"),
        PriceData(_prices([100, 100, 110, 110])),
        event_sink=None,
        cancellation=None,
    )

    assert result.result.status is BacktestStatus.COMPLETED
    assert result.result.equity_curve[-1].equity == 110_000
    assert len(result.result.trades) == 1
    assert result.result.trades[0].entry_date == date(2024, 1, 2)
    assert result.result.trades[0].exit_date == date(2024, 1, 4)
    assert result.result.trades[0].pnl == 10_000
    assert [point.applied_weight for point in result.signals] == [0, 1, 1, 0]
    assert result.result.metrics.trade_count == 1
    assert result.result.metrics.total_return == 0.1


async def test_strategy_cancellation_never_reports_success() -> None:
    cancellation = asyncio.Event()
    events = []

    async def sink(event) -> None:
        events.append(event)
        if event.kind is BacktestEventKind.PROGRESS:
            cancellation.set()

    output = await run_strategy_backtest(
        StrategyBacktestRequest(
            algorithm_key="step_strategy",
            symbols=("AAA",),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 4),
        ),
        StepStrategy(ticker="AAA"),
        PriceData(_prices([100, 100, 110, 110])),
        event_sink=sink,
        cancellation=cancellation,
    )

    assert output.result.status is BacktestStatus.CANCELLED
    assert [event.kind for event in events if event.terminal] == [BacktestEventKind.CANCELLED]
    assert output.result.trades == ()
