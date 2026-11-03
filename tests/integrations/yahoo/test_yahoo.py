"""Yahoo client against a fake of its endpoints, serving fixtures recorded by ai-hedge-fund."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from fin_graph.integrations.base import HttpStatusError, IntegrationError, VerifyStatus
from fin_graph.integrations.yahoo.client import YahooClient
from fin_graph.integrations.yahoo.integration import YahooIntegration
from fin_graph.integrations.yahoo.session import YahooSession

DATA = Path(__file__).parent / "data"
TIMESERIES = {
    "timeseries": {
        "result": [
            {
                "meta": {"type": ["annualFreeCashFlow"]},
                "annualFreeCashFlow": [
                    {"asOfDate": "2023-09-30", "reportedValue": {"raw": 99_600_000_000}},
                    {"asOfDate": "2021-09-25", "reportedValue": {"raw": 93_000_000_000}},
                ],
            },
            {
                "meta": {"type": ["annualOrdinarySharesNumber"]},
                "annualOrdinarySharesNumber": [
                    {"asOfDate": "2023-09-30", "reportedValue": {"raw": 15_550_000_000}},
                    {"asOfDate": "2021-09-25", "reportedValue": {"raw": 16_400_000_000}},
                ],
            },
        ]
    }
}


class FakeYahoo:
    """Serves the recorded fixtures by host and path, counting calls."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.overrides: dict[str, Callable[[httpx.Request], httpx.Response]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        for prefix, handler in self.overrides.items():
            if path.startswith(prefix):
                return handler(request)
        if request.url.host == "fc.yahoo.com":
            return httpx.Response(
                404, headers={"set-cookie": "A3=cookie; Path=/; Domain=.yahoo.com"}
            )
        if path == "/v1/test/getcrumb":
            return httpx.Response(200, text="crumb-123")
        if path.startswith("/v8/finance/chart/"):
            return httpx.Response(200, content=(DATA / "chart.json").read_bytes())
        if path.startswith("/v10/finance/quoteSummary/"):
            return httpx.Response(200, content=(DATA / "quote_summary.json").read_bytes())
        if path.startswith("/ws/fundamentals-timeseries/"):
            return httpx.Response(200, json=TIMESERIES)
        if path == "/rss/2.0/headline":
            return httpx.Response(200, content=(DATA / "rss.xml").read_bytes())
        return httpx.Response(404)

    def count(self, prefix: str) -> int:
        return sum(call.url.path.startswith(prefix) for call in self.calls)


@pytest.fixture
def fake() -> FakeYahoo:
    return FakeYahoo()


def client(fake: FakeYahoo, today: date = date(2024, 1, 20), **kwargs: Any) -> YahooClient:
    session = YahooSession(transport=httpx.MockTransport(fake), backoff=(0, 0))
    return YahooClient(session, today=lambda: today, **kwargs)


async def test_prices_map_the_chart_and_skip_null_rows(fake: FakeYahoo) -> None:
    bars = await client(fake).prices("AAPL", date(2024, 1, 1), date(2024, 1, 31))

    assert len(bars) == 2
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (100.0, 102.0, 99.0, 101.0)
    assert bars[0].volume == 1_000_000 and bars[0].day == date(2024, 1, 2)
    assert await client(fake).prices("AAPL", date(2024, 1, 3), date(2024, 1, 3)) != []
    assert await client(fake).prices("AAPL", date(2024, 2, 1), date(2024, 1, 1)) == []


async def test_quote_summary_sends_the_crumb_and_is_fetched_once(fake: FakeYahoo) -> None:
    yahoo = client(fake)

    await asyncio.gather(
        yahoo.financial_metrics("AAPL", date(2024, 1, 20)),
        yahoo.insider_trades("AAPL", date(2024, 1, 20)),
        yahoo.market_cap("AAPL", date(2024, 1, 20)),
    )

    summaries = [call for call in fake.calls if "quoteSummary" in call.url.path]
    assert len(summaries) == 1
    assert summaries[0].url.params["crumb"] == "crumb-123"
    assert "A3=cookie" in summaries[0].headers.get("cookie", "")


async def test_stale_cookies_reset_and_retry_once(fake: FakeYahoo) -> None:
    answers = iter(
        [
            httpx.Response(401),
            httpx.Response(200, content=(DATA / "quote_summary.json").read_bytes()),
        ]
    )
    fake.overrides["/v10/finance/quoteSummary/"] = lambda request: next(answers)

    metrics = await client(fake).financial_metrics("AAPL", date(2024, 1, 20))

    assert metrics and fake.count("/v1/test/getcrumb") == 2


async def test_rate_limits_back_off_then_fail_readably(fake: FakeYahoo) -> None:
    answers = iter(
        [httpx.Response(429), httpx.Response(200, content=(DATA / "chart.json").read_bytes())]
    )
    fake.overrides["/v8/finance/chart/"] = lambda request: next(answers)
    assert len(await client(fake).prices("AAPL", date(2024, 1, 1), date(2024, 1, 31))) == 2

    fake.overrides["/v8/finance/chart/"] = lambda request: httpx.Response(429)
    with pytest.raises(HttpStatusError, match="returned 429") as error:
        await client(fake).prices("MSFT", date(2024, 1, 1), date(2024, 1, 31))
    assert error.value.status == 429


async def test_only_yahoo_hosts_are_reachable_and_bodies_are_capped(fake: FakeYahoo) -> None:
    session = YahooSession(transport=httpx.MockTransport(fake), backoff=())
    with pytest.raises(IntegrationError, match="unexpected host"):
        await session.get("https://evil.example/steal")

    fake.overrides["/v8/finance/chart/"] = lambda request: httpx.Response(
        200, content=b"x" * (3 * 1024 * 1024)
    )
    with pytest.raises(IntegrationError, match="too large"):
        await client(fake).prices("AAPL", date(2024, 1, 1), date(2024, 1, 31))


async def test_the_ttm_snapshot_is_used_only_when_as_of_is_recent(fake: FakeYahoo) -> None:
    recent = await client(fake).financial_metrics("AAPL", date(2024, 1, 20))
    historical = await client(fake).financial_metrics("AAPL", date(2023, 12, 1))

    snapshot = recent[0]
    assert snapshot.period == "ttm" and snapshot.report_period == date(2024, 1, 20)
    assert snapshot.market_cap == 3_000_000_000_000
    assert snapshot.debt_to_equity == pytest.approx(1.5)  # Yahoo sends 150 (%)
    assert [item.report_period for item in historical] == [
        date(2023, 9, 30),
        date(2022, 9, 24),
        date(2021, 9, 25),
    ]
    assert {item.period for item in historical} == {"annual"}
    assert all(item.report_period <= date(2023, 12, 1) for item in historical)


async def test_annual_statements_count_only_after_the_reporting_lag(fake: FakeYahoo) -> None:
    yahoo = client(fake)

    before = await yahoo.line_items("AAPL", ["revenue"], date(2023, 10, 20))
    after = await yahoo.line_items("AAPL", ["revenue"], date(2023, 11, 20))

    assert [item.report_period for item in before] == [date(2022, 9, 24), date(2021, 9, 25)]
    assert after[0].report_period == date(2023, 9, 30)


async def test_annual_records_remain_unavailable_until_their_synthetic_filing_date(
    fake: FakeYahoo,
) -> None:
    yahoo = client(fake, report_lag_days=45)

    metrics = await yahoo.financial_metrics("AAPL", date(2024, 1, 20))
    items = await yahoo.line_items("AAPL", ["revenue"], date(2024, 1, 20))
    annual_metric = next(item for item in metrics if item.period == "annual")

    assert annual_metric.filing_date == annual_metric.report_period + timedelta(days=45)
    assert items[0].filing_date == items[0].report_period + timedelta(days=45)


async def test_line_items_merge_summary_and_timeseries(fake: FakeYahoo) -> None:
    items = await client(fake).line_items(
        "AAPL",
        [
            "revenue",
            "free_cash_flow",
            "outstanding_shares",
            "interest_expense",
            "capital_expenditure",
            "unknown_line",
        ],
        date(2024, 1, 20),
    )

    latest, _, oldest = items
    assert latest.value("revenue") == 383_000_000_000
    assert latest.value("free_cash_flow") == 99_600_000_000  # timeseries wins for FCF
    assert latest.value("outstanding_shares") == 15_550_000_000
    assert latest.value("interest_expense") == 3_800_000_000  # sign normalised
    assert (
        latest.value("capital_expenditure") is not None and latest.value("capital_expenditure") < 0
    )
    assert latest.value("unknown_line") is None
    assert oldest.report_period == date(2021, 9, 25) and oldest.value("revenue") is None


async def test_insider_sales_are_negative_and_filtered_by_date(fake: FakeYahoo) -> None:
    trades = await client(fake).insider_trades("AAPL", date(2024, 1, 20), start=date(2024, 1, 1))

    assert [(trade.name, trade.transaction_shares) for trade in trades] == [
        ("COOK TIMOTHY D", -50_000),
        ("DOE JANE", 1_000),
    ]
    assert trades[0].transaction_price_per_share == pytest.approx(170.0)
    assert await client(fake).insider_trades("AAPL", date(2023, 1, 1)) == []


async def test_news_come_from_the_rss_feed_point_in_time(fake: FakeYahoo) -> None:
    news = await client(fake).company_news("AAPL", date(2024, 1, 12), start=date(2024, 1, 1))

    assert [item.title for item in news] == ["Apple beats earnings expectations"]
    fake.overrides["/rss/2.0/headline"] = lambda request: httpx.Response(200, text="<not-rss")
    with pytest.raises(IntegrationError, match="not valid RSS"):
        await client(fake).company_news("MSFT", date(2024, 1, 12))


async def test_market_cap_live_when_recent_else_close_times_shares(fake: FakeYahoo) -> None:
    live = await client(fake).market_cap("AAPL", date(2024, 1, 20))
    historical = await client(fake, today=date(2024, 6, 1)).market_cap("AAPL", date(2024, 1, 3))

    assert live == 3_000_000_000_000
    assert historical == pytest.approx(102.5 * 15_000_000_000)


async def test_integration_is_on_by_default_and_verifies_with_aapl_prices(
    fake: FakeYahoo, monkeypatch: pytest.MonkeyPatch
) -> None:
    integration = YahooIntegration()
    settings = integration.load_settings(None)
    assert settings is not None and integration.tools() == []
    monkeypatch.setenv("YAHOO_ENABLED", "false")
    assert integration.load_settings(None) is None

    fake.overrides["/v8/finance/chart/"] = lambda request: httpx.Response(
        200,
        json={
            "chart": {
                "result": [
                    {
                        "timestamp": [int(request.url.params["period2"]) - 86_400 * 2],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [1],
                                    "high": [2],
                                    "low": [0.5],
                                    "close": [1.5],
                                    "volume": [9],
                                }
                            ]
                        },
                    }
                ]
            }
        },
    )
    result = await integration.verify(client(fake))
    assert result.status is VerifyStatus.OK and "1.50" in result.detail


def test_recorded_fixtures_are_valid_json() -> None:
    assert json.loads((DATA / "quote_summary.json").read_text(encoding="utf-8"))["quoteSummary"]
