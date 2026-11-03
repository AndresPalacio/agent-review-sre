"""Shared point-in-time data contracts for every v2 quantitative model."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from fin_graph.domain.market import (
    CompanyNews,
    FinancialMetrics,
    InsiderTrade,
    LineItem,
    Price,
    latest_first,
    prices_until,
)


@runtime_checkable
class MarketDataClient(Protocol):
    """Async provider contract; absence is empty/None, infrastructure failures raise."""

    async def prices(self, ticker: str, start: date, end: date) -> list[Price]: ...

    async def financial_metrics(
        self, ticker: str, as_of: date, *, period: str = "ttm", limit: int = 10
    ) -> list[FinancialMetrics]: ...

    async def line_items(
        self,
        ticker: str,
        names: Sequence[str],
        as_of: date,
        *,
        period: str = "annual",
        limit: int = 10,
    ) -> list[LineItem]: ...

    async def insider_trades(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 1000
    ) -> list[InsiderTrade]: ...

    async def company_news(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 100
    ) -> list[CompanyNews]: ...

    async def market_cap(self, ticker: str, as_of: date) -> float | None: ...


def adjusted_close(price: Price) -> float:
    """The economically comparable close for features and return calculations."""

    return price.effective_close


class FundamentalSnapshot(BaseModel):
    """Immutable filed history and deterministic aggregates as known on ``as_of``."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: date
    periods: tuple[FinancialMetrics, ...]
    roe_average: float | None = None
    net_margin_average: float | None = None
    gross_margin_trend: float | None = None
    book_value_per_share_cagr: float | None = None
    debt_to_equity_latest: float | None = None
    market_cap_latest: float | None = None


class TechnicalSnapshot(BaseModel):
    """Immutable adjusted-price history available on one simulation date."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: date
    prices: tuple[Price, ...]
    closes: tuple[float, ...]


class PointInTimeData:
    """A provider view that cannot return observations after one immutable cutoff."""

    def __init__(self, client: MarketDataClient, as_of: date) -> None:
        self.client = client
        self.as_of = as_of

    async def prices(self, ticker: str, start: date, end: date) -> list[Price]:
        bounded_end = min(end, self.as_of)
        if start > bounded_end:
            return []
        values = await self.client.prices(ticker, start, bounded_end)
        return [item for item in prices_until(values, bounded_end) if item.day >= start]

    async def financial_metrics(
        self, ticker: str, as_of: date, *, period: str = "ttm", limit: int = 10
    ) -> list[FinancialMetrics]:
        cutoff = min(as_of, self.as_of)
        values = await self.client.financial_metrics(ticker, cutoff, period=period, limit=limit)
        return latest_first(values, cutoff)[:limit]

    async def line_items(
        self,
        ticker: str,
        names: Sequence[str],
        as_of: date,
        *,
        period: str = "annual",
        limit: int = 10,
    ) -> list[LineItem]:
        cutoff = min(as_of, self.as_of)
        values = await self.client.line_items(ticker, names, cutoff, period=period, limit=limit)
        return latest_first(values, cutoff)[:limit]

    async def insider_trades(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 1000
    ) -> list[InsiderTrade]:
        cutoff = min(as_of, self.as_of)
        values = await self.client.insider_trades(ticker, cutoff, start=start, limit=limit)
        known = [
            item
            for item in values
            if item.filing_date <= cutoff and (start is None or item.filing_date >= start)
        ]
        return sorted(known, key=lambda item: item.filing_date, reverse=True)[:limit]

    async def company_news(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 100
    ) -> list[CompanyNews]:
        cutoff = min(as_of, self.as_of)
        values = await self.client.company_news(ticker, cutoff, start=start, limit=limit)
        known = [
            item
            for item in values
            if item.date.date() <= cutoff and (start is None or item.date.date() >= start)
        ]
        return sorted(known, key=lambda item: item.date, reverse=True)[:limit]

    async def market_cap(self, ticker: str, as_of: date) -> float | None:
        return await self.client.market_cap(ticker, min(as_of, self.as_of))
