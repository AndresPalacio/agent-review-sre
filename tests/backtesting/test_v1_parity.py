from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from fin_graph.backtesting.models import BacktestEvent, BacktestRequest, BacktestStatus
from fin_graph.backtesting.service import PointInTimeDataView, run_v1_backtest
from fin_graph.compat.ai_hedge_fund.models import (
    LegacyHedgeFundResult,
    LegacyPortfolioDecision,
)
from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import Price
from fin_graph.domain.orders import OrderAction
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.requests.model import AnalysisRequest

FIXTURE = Path(__file__).parents[1] / "compat" / "fixtures" / "v1" / "backtests" / "scenarios.json"
CASES: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
START = date(2025, 1, 6)
DAYS = tuple(START + timedelta(days=index) for index in range(4))


def bar(day: date, close: float) -> Price:
    return Price(
        time=datetime(day.year, day.month, day.day, 21, tzinfo=UTC),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000,
    )


class DataClient:
    def __init__(self, histories: dict[str, tuple[Price, ...]]) -> None:
        self.histories = histories

    async def price_history(
        self, ticker: str, start_date: date, end_date: date
    ) -> tuple[Price, ...]:
        return self.histories[ticker]

    async def company_snapshot(
        self, ticker: str, start_date: date, end_date: date
    ) -> CompanySnapshot:
        history = self.histories[ticker]
        return CompanySnapshot(ticker=ticker, as_of=history[-1].day, prices=history)


class ScenarioRunner:
    def __init__(self, plans: list[dict[str, list[Any]]]) -> None:
        self.plans = plans
        self.calls = 0

    async def __call__(
        self,
        request: AnalysisRequest,
        portfolio: Portfolio,
        data: PointInTimeDataView,
        *,
        model: str,
    ) -> LegacyHedgeFundResult:
        assert model == "gpt-4.1"
        plan = self.plans[self.calls]
        self.calls += 1
        decisions: dict[str, LegacyPortfolioDecision] = {}
        for ticker in request.tickers:
            action, quantity = plan.get(ticker, ["hold", 0])
            decisions[ticker] = LegacyPortfolioDecision(
                action=OrderAction(action),
                quantity=quantity,
                confidence=80,
                reasoning="fixture",
            )
        return LegacyHedgeFundResult(decisions=decisions, analyst_signals={})


@pytest.mark.parametrize("case_name", CASES)
async def test_v1_long_short_scenarios_match_golden_results(case_name: str) -> None:
    case = CASES[case_name]
    histories = {
        ticker: tuple(bar(day, close) for day, close in zip(DAYS, closes, strict=True))
        for ticker, closes in case["prices"].items()
    }
    histories["SPY"] = tuple(
        bar(day, close) for day, close in zip(DAYS, [100.0, 101.0, 99.0, 102.0], strict=True)
    )
    events: list[BacktestEvent] = []
    result = await run_v1_backtest(
        BacktestRequest(
            tickers=list(case["prices"]),
            start_date=START,
            end_date=DAYS[-1],
            initial_capital=10_000,
            margin_requirement=0.5,
        ),
        ScenarioRunner(case["plans"]),
        DataClient(histories),
        event_sink=events.append,
        cancellation=None,
    )

    assert result.status is BacktestStatus.COMPLETED
    assert [snapshot.portfolio_value for snapshot in result.snapshots] == pytest.approx(
        case["portfolio_values"]
    )
    assert result.final_portfolio.net_liquidation_value(
        result.snapshots[-1].current_prices
    ) == pytest.approx(case["final_value"])
    assert result.metrics.total_return_pct == pytest.approx(case["total_return_pct"])
    assert result.metrics.benchmark_return_pct == pytest.approx(2.0)
    assert result.metrics.sharpe_ratio == pytest.approx(case["sharpe_ratio"])
    assert result.metrics.max_drawdown_pct == pytest.approx(case["max_drawdown_pct"])
    assert result.metrics.max_drawdown_date == date.fromisoformat(case["max_drawdown_date"])
    assert result.metrics.win_rate_pct == pytest.approx(case["win_rate_pct"])
    assert sum(event.terminal for event in events) == 1
    assert events[-1].kind.value == "complete"


async def test_pre_cancelled_backtest_returns_one_cancelled_terminal_event() -> None:
    cancellation = asyncio.Event()
    cancellation.set()
    events: list[BacktestEvent] = []
    request = BacktestRequest(tickers=["AAPL"], start_date=START, end_date=DAYS[-1])

    result = await run_v1_backtest(
        request,
        ScenarioRunner([]),
        DataClient({}),
        event_sink=events.append,
        cancellation=cancellation,
    )

    assert result.status is BacktestStatus.CANCELLED
    assert result.snapshots == ()
    assert sum(event.terminal for event in events) == 1
    assert events[-1].kind.value == "cancelled"


async def test_task_cancellation_propagates_and_emits_one_terminal_event() -> None:
    started = asyncio.Event()

    class BlockingRunner:
        async def __call__(
            self,
            request: AnalysisRequest,
            portfolio: Portfolio,
            data: PointInTimeDataView,
            *,
            model: str,
        ) -> LegacyHedgeFundResult:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    histories = {
        "AAPL": tuple(bar(day, 100.0) for day in DAYS),
        "SPY": tuple(bar(day, 100.0) for day in DAYS),
    }
    events: list[BacktestEvent] = []
    task = asyncio.create_task(
        run_v1_backtest(
            BacktestRequest(tickers=["AAPL"], start_date=START, end_date=DAYS[-1]),
            BlockingRunner(),
            DataClient(histories),
            event_sink=events.append,
            cancellation=None,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert sum(event.terminal for event in events) == 1
    assert events[-1].kind.value == "cancelled"


async def test_cooperative_cancellation_does_not_report_future_benchmark() -> None:
    cancellation = asyncio.Event()
    events: list[BacktestEvent] = []

    def sink(event: BacktestEvent) -> None:
        events.append(event)
        if event.kind.value == "progress":
            cancellation.set()

    histories = {
        "AAPL": tuple(bar(day, 100.0) for day in DAYS),
        "SPY": tuple(
            bar(day, close) for day, close in zip(DAYS, [100.0, 101.0, 99.0, 102.0], strict=True)
        ),
    }
    result = await run_v1_backtest(
        BacktestRequest(tickers=["AAPL"], start_date=START, end_date=DAYS[-1]),
        ScenarioRunner([{}]),
        DataClient(histories),
        event_sink=sink,
        cancellation=cancellation,
    )

    assert result.status is BacktestStatus.CANCELLED
    assert len(result.snapshots) == 1
    assert result.metrics.benchmark_return_pct == 0
    assert sum(event.terminal for event in events) == 1


async def test_data_failure_emits_one_error_terminal_and_propagates() -> None:
    class FailingClient(DataClient):
        async def price_history(
            self, ticker: str, start_date: date, end_date: date
        ) -> tuple[Price, ...]:
            raise RuntimeError("fixture data failure")

    events: list[BacktestEvent] = []
    with pytest.raises(RuntimeError, match="fixture data failure"):
        await run_v1_backtest(
            BacktestRequest(tickers=["AAPL"], start_date=START, end_date=DAYS[-1]),
            ScenarioRunner([]),
            FailingClient({}),
            event_sink=events.append,
            cancellation=None,
        )

    assert sum(event.terminal for event in events) == 1
    assert events[-1].kind.value == "error"
