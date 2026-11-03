from __future__ import annotations

from datetime import UTC, date, datetime

from fin_graph.backtesting.models import BacktestEvent, BacktestRequest
from fin_graph.backtesting.service import PointInTimeDataView, run_v1_backtest
from fin_graph.compat.ai_hedge_fund.models import (
    LegacyHedgeFundResult,
    LegacyPortfolioDecision,
)
from fin_graph.domain.company import CompanySnapshot
from fin_graph.domain.market import FinancialMetrics, Price
from fin_graph.domain.orders import OrderAction
from fin_graph.domain.portfolio import Portfolio
from fin_graph.domain.requests.model import AnalysisRequest


def bar(day: date, close: float) -> Price:
    return Price(
        time=datetime(day.year, day.month, day.day, 21, tzinfo=UTC),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000,
    )


class LeakyDataClient:
    def __init__(self) -> None:
        self.days = (date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8))
        self.future = date(2025, 1, 10)
        self.queries: list[tuple[str, date]] = []
        self.histories = {
            "AAPL": tuple([*(bar(day, 100.0) for day in self.days), bar(self.future, 1_000.0)]),
            "SPY": tuple([*(bar(day, 100.0) for day in self.days), bar(self.future, 500.0)]),
        }

    async def price_history(
        self, ticker: str, start_date: date, end_date: date
    ) -> tuple[Price, ...]:
        self.queries.append((ticker, end_date))
        return self.histories[ticker]

    async def company_snapshot(
        self, ticker: str, start_date: date, end_date: date
    ) -> CompanySnapshot:
        return CompanySnapshot(
            ticker=ticker,
            as_of=self.future,
            prices=self.histories[ticker],
            metrics=(
                FinancialMetrics(
                    ticker=ticker,
                    report_period=date(2025, 1, 5),
                    revenue_growth=-0.2,
                ),
                FinancialMetrics(
                    ticker=ticker,
                    report_period=self.future,
                    revenue_growth=0.8,
                ),
            ),
        )


class InspectingRunner:
    def __init__(self) -> None:
        self.as_of_dates: list[date] = []
        self.growth_seen: list[float | None] = []

    async def __call__(
        self,
        request: AnalysisRequest,
        portfolio: Portfolio,
        data: PointInTimeDataView,
    ) -> LegacyHedgeFundResult:
        snapshot = data.company_snapshot("AAPL")
        assert snapshot.as_of == request.as_of == data.as_of
        assert all(item.day <= request.as_of for item in data.price_history("AAPL"))
        assert all(item.report_period <= request.as_of for item in snapshot.metrics)
        self.as_of_dates.append(request.as_of)
        latest_growth = snapshot.metrics[-1].revenue_growth if snapshot.metrics else None
        self.growth_seen.append(latest_growth)
        action = OrderAction.BUY if latest_growth and latest_growth > 0 else OrderAction.HOLD
        return LegacyHedgeFundResult(
            decisions={
                "AAPL": LegacyPortfolioDecision(
                    action=action,
                    quantity=1 if action is OrderAction.BUY else 0,
                    confidence=80,
                    reasoning="point in time",
                )
            },
            analyst_signals={},
        )


async def test_future_filing_and_price_bar_never_reach_the_runner() -> None:
    client = LeakyDataClient()
    runner = InspectingRunner()
    events: list[BacktestEvent] = []
    request = BacktestRequest(
        tickers=["AAPL"],
        start_date=client.days[0],
        end_date=client.days[-1],
        initial_capital=10_000,
    )

    result = await run_v1_backtest(
        request,
        runner,
        client,
        event_sink=events.append,
        cancellation=None,
    )

    assert runner.as_of_dates == list(client.days)
    assert runner.growth_seen == [-0.2, -0.2, -0.2]
    assert all(snapshot.executed_trades["AAPL"] == 0 for snapshot in result.snapshots)
    assert all(end_date == request.end_date for _, end_date in client.queries)
    assert result.metrics.benchmark_return_pct == 0.0
    assert sum(event.terminal for event in events) == 1
