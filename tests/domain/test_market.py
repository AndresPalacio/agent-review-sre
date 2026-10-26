from __future__ import annotations

from datetime import UTC, date, datetime

from tests.domain.quant.synthetic import insider_trades, metrics, news, prices

from fin_graph.domain.market import (
    CompanyNews,
    InsiderTrade,
    LineItem,
    Price,
    latest_first,
    prices_until,
    sorted_prices,
)


def test_recorded_fixtures_load_with_typed_dates() -> None:
    bars = prices("AAPL")

    assert bars[0].time == datetime(2024, 3, 1, 5, tzinfo=UTC)
    assert bars[0].day == date(2024, 3, 1)
    assert metrics("AAPL")[0].report_period == date(2023, 12, 30)
    assert isinstance(news("TSLA")[0].date, datetime)
    assert insider_trades("MSFT")[0].filing_date.year == 2024


def test_point_in_time_views_never_see_the_future() -> None:
    bars = list(reversed(prices("AAPL")))

    ordered = sorted_prices(bars)
    cut = prices_until(bars, date(2024, 3, 5))

    assert [bar.day for bar in ordered] == sorted(bar.day for bar in bars)
    assert max(bar.day for bar in cut) == date(2024, 3, 5)
    assert all(
        item.report_period <= date(2023, 6, 30)
        for item in latest_first(metrics("AAPL"), date(2023, 6, 30))
    )
    assert latest_first(metrics("AAPL"), date(2024, 3, 8))[0].report_period == date(2023, 12, 30)


def test_line_items_keep_any_named_line_and_reject_non_numbers() -> None:
    item = LineItem(
        ticker="AAPL",
        report_period=date(2023, 9, 30),
        capital_expenditure=-10.9e9,
        note="restated",
        flag=True,
    )

    assert item.value("capital_expenditure") == -10.9e9
    assert item.value("note") is None and item.value("flag") is None
    assert item.value("missing") is None


def test_volume_and_naive_timestamps_are_normalised() -> None:
    bar = Price(time=datetime(2024, 1, 2), open=1, high=2, low=0.5, close=1.5, volume=10.0)

    assert bar.time.tzinfo is UTC and bar.volume == 10


def test_news_and_insiders_accept_provider_variants() -> None:
    item = CompanyNews(ticker="AAPL", title="t", date="2024-03-08T22:45:22Z", sentiment="positive")
    trade = InsiderTrade(ticker="AAPL", filing_date="2024-03-01T00:00:00", transaction_shares=-5)

    assert item.date.tzinfo is not None
    assert trade.filing_date == date(2024, 3, 1)
