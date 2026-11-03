from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from fin_graph.backtesting.models import BacktestEventKind, BacktestStatus
from fin_graph.backtesting.quant import AlphaBacktestRequest, run_alpha_backtest
from fin_graph.domain.market import Price
from fin_graph.quant.signals.base import AlphaModel, Signal


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


class ScriptedAlpha(AlphaModel):
    def __init__(self) -> None:
        self.observed: list[tuple[date, date | None]] = []

    @property
    def name(self) -> str:
        return "scripted"

    async def predict(self, ticker: str, as_of: date, data) -> Signal:
        visible = await data.prices(ticker, date(2020, 1, 1), date(2030, 1, 1))
        self.observed.append((as_of, max((row.day for row in visible), default=None)))
        values = {
            date(2024, 1, 1): 1.0,
            date(2024, 1, 2): 1.0,
            date(2024, 1, 3): 0.0,
            date(2024, 1, 4): -1.0,
            date(2024, 1, 5): 0.0,
        }
        return Signal(
            model_name=self.name,
            ticker=ticker,
            as_of=as_of,
            value=values[as_of],
            reasoning=f"view-{as_of.isoformat()}",
            metadata={"date": as_of.isoformat()},
        )


async def test_alpha_reference_trades_metrics_and_point_in_time_boundary() -> None:
    model = ScriptedAlpha()
    result = await run_alpha_backtest(
        AlphaBacktestRequest(
            tickers=("AAA",),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 5),
            initial_capital=100_000,
            per_trade=10_000,
            threshold=0.5,
            holding_days=1,
        ),
        model,
        PriceData(_prices([100, 110, 90, 100, 90])),
        event_sink=None,
        cancellation=None,
    )

    assert result.status is BacktestStatus.COMPLETED
    assert [(item.direction, item.entry_date, item.exit_date) for item in result.trades] == [
        ("long", date(2024, 1, 1), date(2024, 1, 2)),
        ("short", date(2024, 1, 4), date(2024, 1, 5)),
    ]
    assert [item.pnl for item in result.trades] == [1_000.0, 1_000.0]
    assert result.equity_curve == (100_000.0, 101_000.0, 102_000.0)
    assert result.metrics is not None
    assert result.metrics.total_return_pct == 0.02
    assert result.metrics.win_rate == 1.0
    assert all(
        last_visible is None or last_visible <= as_of for as_of, last_visible in model.observed
    )


async def test_alpha_cancellation_emits_exactly_one_terminal_event() -> None:
    cancellation = asyncio.Event()
    events = []

    async def sink(event) -> None:
        events.append(event)
        if event.kind is BacktestEventKind.PROGRESS:
            cancellation.set()

    result = await run_alpha_backtest(
        AlphaBacktestRequest(
            tickers=("AAA",),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 5),
            holding_days=1,
        ),
        ScriptedAlpha(),
        PriceData(_prices([100, 110, 90, 100, 90])),
        event_sink=sink,
        cancellation=cancellation,
    )

    terminal = [event for event in events if event.terminal]
    assert result.status is BacktestStatus.CANCELLED
    assert len(terminal) == 1
    assert terminal[0].kind is BacktestEventKind.CANCELLED
    assert [event.sequence for event in events] == list(range(len(events)))


def test_alpha_request_rejects_invalid_ranges_and_mechanics() -> None:
    with pytest.raises(ValueError, match="start_date"):
        AlphaBacktestRequest(
            tickers=("AAA",),
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 1),
        )
    with pytest.raises(ValueError):
        AlphaBacktestRequest(
            tickers=("AAA",),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            holding_days=0,
        )
