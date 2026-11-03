from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from fin_graph.domain.market import Price
from fin_graph.quant.event_study.engine import run_event_study
from fin_graph.quant.event_study.models import EventStudyRequest, EventStudyResult
from fin_graph.quant.event_study.plot import plot_event_study
from fin_graph.quant.event_study.stats import bootstrap_ci, ttest_cars
from fin_graph.quant.signals.pead import EarningsData, EarningsRecord


class Data:
    def __init__(self) -> None:
        self.series: dict[str, list[Price]] = {}
        self.events: dict[str, list[EarningsRecord]] = {}

    async def prices(self, ticker: str, start: date, end: date):
        return [row for row in self.series.get(ticker, []) if start <= row.day <= end]

    async def earnings_history(self, ticker: str, *, limit: int = 12):
        return self.events.get(ticker, [])[:limit]

    async def financial_metrics(self, ticker: str, as_of: date, **kwargs):
        return []

    async def line_items(self, ticker: str, names, as_of: date, **kwargs):
        return []

    async def insider_trades(self, ticker: str, as_of: date, **kwargs):
        return []

    async def company_news(self, ticker: str, as_of: date, **kwargs):
        return []

    async def market_cap(self, ticker: str, as_of: date):
        return None


def prices(returns: list[float], first: date) -> list[Price]:
    close = 100.0
    rows: list[Price] = []
    for index, change in enumerate([0.0, *returns]):
        close *= 1 + change
        rows.append(
            Price(
                time=datetime.combine(first + timedelta(days=index), datetime.min.time(), UTC),
                open=close,
                high=close,
                low=close,
                close=close,
                adj_close=close,
                volume=1_000,
            )
        )
    return rows


async def test_event_study_aligns_benchmark_and_computes_abnormal_cars() -> None:
    first = date(2024, 1, 1)
    market_returns = [0.001 + 0.0002 * math.sin(index / 7) for index in range(330)]
    stock_returns = list(market_returns)
    stock_returns[270] += 0.05
    data = Data()
    data.series = {"SPY": prices(market_returns, first), "CASE": prices(stock_returns, first)}
    filing = first + timedelta(days=270)
    data.events["CASE"] = [
        EarningsRecord(
            ticker="CASE",
            report_period=filing - timedelta(days=30),
            filing_date=filing,
            source_type="8-K",
            quarterly=EarningsData(eps_surprise="BEAT"),
        )
    ]
    request = EventStudyRequest(
        tickers=("CASE",),
        as_of=first + timedelta(days=330),
        price_start=first,
        n_bootstrap=100,
        seed=7,
    )

    result = await run_event_study(request, data)

    assert len(result.events) == 1
    event = result.events[0]
    assert event.market_model.beta == pytest.approx(1, abs=1e-6)
    assert event.car_0_1 == pytest.approx(0.05, abs=1e-6)
    assert event.eps_surprise == "BEAT"


async def test_event_near_dataset_edge_is_skipped_not_misaligned() -> None:
    first = date(2024, 1, 1)
    data = Data()
    rows = prices([0.001] * 100, first)
    data.series = {"SPY": rows, "EARLY": rows}
    data.events["EARLY"] = [
        EarningsRecord(
            ticker="EARLY",
            report_period=first,
            filing_date=first + timedelta(days=20),
            source_type="8-K",
        )
    ]
    result = await run_event_study(
        EventStudyRequest(
            tickers=("EARLY",),
            as_of=first + timedelta(days=100),
            price_start=first,
            n_bootstrap=10,
            seed=1,
        ),
        data,
    )

    assert result.events == ()
    assert result.skipped_tickers == ("EARLY",)


def test_event_statistics_are_seeded_and_plot_is_written(tmp_path: Path) -> None:
    cars = np.asarray([0.01, 0.03, 0.02, -0.01])
    interval = bootstrap_ci(cars, n_bootstrap=500, seed=8)
    assert interval == bootstrap_ci(cars, n_bootstrap=500, seed=8)
    statistic, probability = ttest_cars(cars)
    assert math.isfinite(statistic) and 0 <= probability <= 1

    output = plot_event_study(
        EventStudyResult(market_ticker="SPY", as_of=date(2025, 1, 1), seed=8),
        tmp_path / "event-study.png",
    )
    assert output.is_file()
