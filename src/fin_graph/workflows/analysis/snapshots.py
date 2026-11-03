"""Point-in-time company snapshots for the compatibility persona team."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta

from fin_graph.domain.company import CompanySnapshot
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.market_data import ANALYST_LINE_ITEMS, MarketDataClient

PERSONA_LINE_ITEMS = tuple(
    dict.fromkeys(
        (
            *ANALYST_LINE_ITEMS,
            "debt_to_equity",
            "goodwill_and_intangible_assets",
            "operating_expense",
            "operating_margin",
            "research_and_development",
            "return_on_invested_capital",
        )
    )
)


@dataclass(frozen=True)
class MarketSnapshotServices:
    """Load every deterministic persona input from the configured market-data client."""

    price_lookback_days: int = 1095
    sentiment_lookback_days: int = 365
    history_limit: int = 10

    async def company_snapshot(
        self, ticker: str, as_of: date, context: RunContext
    ) -> CompanySnapshot:
        engine = context.client("analytics", AnalyticsEngine)
        client = context.clients.get(engine.data_integration)
        if not isinstance(client, MarketDataClient):
            raise TypeError(f"{engine.data_integration} is not a market-data client")
        price_start = as_of - timedelta(days=self.price_lookback_days)
        sentiment_start = as_of - timedelta(days=self.sentiment_lookback_days)

        async with asyncio.TaskGroup() as group:
            prices = group.create_task(client.prices(ticker, price_start, as_of))
            metrics = group.create_task(
                client.financial_metrics(ticker, as_of, period="ttm", limit=self.history_limit)
            )
            line_items = group.create_task(
                client.line_items(
                    ticker,
                    PERSONA_LINE_ITEMS,
                    as_of,
                    period="annual",
                    limit=self.history_limit,
                )
            )
            insider_trades = group.create_task(
                client.insider_trades(
                    ticker,
                    as_of,
                    start=sentiment_start,
                    limit=1000,
                )
            )
            news = group.create_task(
                client.company_news(ticker, as_of, start=sentiment_start, limit=100)
            )
            market_cap = group.create_task(client.market_cap(ticker, as_of))

        return CompanySnapshot(
            ticker=ticker,
            as_of=as_of,
            prices=tuple(prices.result()),
            metrics=tuple(metrics.result()),
            line_items=tuple(line_items.result()),
            insider_trades=tuple(insider_trades.result()),
            news=tuple(news.result()),
            market_cap=market_cap.result(),
        )
