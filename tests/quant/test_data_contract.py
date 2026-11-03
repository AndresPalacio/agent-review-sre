from __future__ import annotations

from datetime import UTC, date, datetime

from fin_graph.domain.market import FinancialMetrics, LineItem, Price
from fin_graph.quant.data import PointInTimeData, adjusted_close
from fin_graph.quant.features import build_fundamental_snapshot, build_technical_snapshot


class Data:
    def __init__(self) -> None:
        self.prices_data = [
            Price(
                time=datetime(2024, 1, 1, tzinfo=UTC),
                open=10,
                high=11,
                low=9,
                close=10,
                adj_close=8,
                volume=100,
            ),
            Price(
                time=datetime(2024, 1, 3, tzinfo=UTC),
                open=20,
                high=21,
                low=19,
                close=20,
                adj_close=18,
                volume=100,
            ),
        ]
        self.metrics_data = [
            FinancialMetrics(
                ticker="CASE",
                report_period=date(2023, 12, 31),
                filing_date=date(2024, 1, 2),
                return_on_equity=0.2,
                gross_margin=0.4,
                book_value_per_share=10,
            ),
            FinancialMetrics(
                ticker="CASE",
                report_period=date(2023, 9, 30),
                filing_date=date(2024, 1, 1),
                return_on_equity=0.1,
                gross_margin=0.3,
                book_value_per_share=8,
            ),
        ]
        self.items_data = [
            LineItem(
                ticker="CASE",
                report_period=date(2023, 12, 31),
                filing_date=date(2024, 1, 2),
                revenue=200,
            ),
            LineItem(
                ticker="CASE",
                report_period=date(2023, 9, 30),
                filing_date=date(2024, 1, 1),
                revenue=100,
            ),
        ]

    async def prices(self, ticker: str, start: date, end: date):
        return self.prices_data

    async def financial_metrics(self, ticker: str, as_of: date, **kwargs):
        return self.metrics_data

    async def line_items(self, ticker: str, names, as_of: date, **kwargs):
        return self.items_data

    async def insider_trades(self, ticker: str, as_of: date, **kwargs):
        return []

    async def company_news(self, ticker: str, as_of: date, **kwargs):
        return []

    async def market_cap(self, ticker: str, as_of: date):
        return 1_000.0


def test_adjusted_close_is_preferred_with_raw_close_fallback() -> None:
    adjusted = Price(
        time=datetime(2024, 1, 1, tzinfo=UTC),
        open=1,
        high=1,
        low=1,
        close=10,
        adj_close=8,
        volume=1,
    )
    raw = adjusted.model_copy(update={"adj_close": None})

    assert adjusted_close(adjusted) == 8
    assert adjusted_close(raw) == 10


async def test_point_in_time_view_filters_future_bars_and_filing_dates() -> None:
    view = PointInTimeData(Data(), date(2024, 1, 1))

    assert [item.day for item in await view.prices("CASE", date(2023, 1, 1), date(2099, 1, 1))] == [
        date(2024, 1, 1)
    ]
    assert [
        item.filing_date for item in await view.financial_metrics("CASE", date(2099, 1, 1))
    ] == [date(2024, 1, 1)]
    assert [
        item.value("revenue") for item in await view.line_items("CASE", (), date(2099, 1, 1))
    ] == [100]


async def test_fundamental_and_technical_snapshots_are_point_in_time_and_adjusted() -> None:
    data = Data()
    fundamentals = await build_fundamental_snapshot(
        "CASE", date(2024, 1, 2), data, minimum_periods=2
    )
    technicals = await build_technical_snapshot(
        "CASE", date(2024, 1, 1), data, start=date(2023, 1, 1)
    )

    assert fundamentals.roe_average == 0.15
    assert fundamentals.gross_margin_trend == 0.1
    assert fundamentals.book_value_per_share_cagr is not None
    assert technicals.closes == (8.0,)
    assert technicals.prices[0].adj_close == 8
