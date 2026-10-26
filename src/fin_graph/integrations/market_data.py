"""The interface every market-data provider implements, a per-run cache, and an offline provider.

Analysts read data only through :class:`MarketDataClient`, so Yahoo, Financial Datasets and
recorded fixtures are interchangeable. Every method is point in time: nothing dated after
``as_of`` comes back. A provider with no data returns an empty list or ``None``. A provider
that fails raises :class:`IntegrationError` with a message the model can read and that holds
no credentials.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Hashable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from fin_graph.domain.market import (
    CompanyNews,
    FinancialMetrics,
    InsiderTrade,
    LineItem,
    Price,
    latest_first,
    prices_until,
)
from fin_graph.integrations.base import IntegrationError

#: Statement lines the analysts ask for; a provider may return more.
ANALYST_LINE_ITEMS: tuple[str, ...] = (
    "revenue",
    "net_income",
    "gross_profit",
    "gross_margin",
    "operating_income",
    "ebit",
    "ebitda",
    "interest_expense",
    "depreciation_and_amortization",
    "capital_expenditure",
    "free_cash_flow",
    "working_capital",
    "total_debt",
    "cash_and_equivalents",
    "total_assets",
    "total_liabilities",
    "current_assets",
    "current_liabilities",
    "shareholders_equity",
    "outstanding_shares",
    "earnings_per_share",
    "book_value_per_share",
    "dividends_and_other_cash_distributions",
    "issuance_or_purchase_of_equity_shares",
)


class MarketDataClient(ABC):
    """Read-only, point-in-time market data for one run."""

    provider: str

    @abstractmethod
    async def prices(self, ticker: str, start: date, end: date) -> list[Price]:
        """Daily bars in ``[start, end]``, oldest first."""

    @abstractmethod
    async def financial_metrics(
        self, ticker: str, as_of: date, *, period: str = "ttm", limit: int = 10
    ) -> list[FinancialMetrics]:
        """Ratio snapshots known on ``as_of``, newest first."""

    @abstractmethod
    async def line_items(
        self,
        ticker: str,
        names: Sequence[str],
        as_of: date,
        *,
        period: str = "annual",
        limit: int = 10,
    ) -> list[LineItem]:
        """Statement lines known on ``as_of``, newest first."""

    @abstractmethod
    async def insider_trades(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 1000
    ) -> list[InsiderTrade]:
        """Insider filings on or before ``as_of`` (and after ``start``), newest first."""

    @abstractmethod
    async def company_news(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 100
    ) -> list[CompanyNews]:
        """Headlines on or before ``as_of`` (and after ``start``), newest first."""

    @abstractmethod
    async def market_cap(self, ticker: str, as_of: date) -> float | None:
        """Market capitalisation on ``as_of``, or ``None`` when unknown."""


class RunMemo:
    """Per-run cache that also coalesces concurrent identical calls into one request.

    Parallel analysts asking for the same ticker share a single fetch. A failure is cached
    too, so a broken endpoint is not hammered within the run; a fresh client per run is the
    retry.
    """

    def __init__(self) -> None:
        self._tasks: dict[Hashable, asyncio.Task[Any]] = {}

    async def get[T](self, key: Hashable, load: Callable[[], Awaitable[T]]) -> T:
        task = self._tasks.get(key)
        if task is None or task.get_loop() is not asyncio.get_running_loop():
            task = asyncio.ensure_future(load())
            self._tasks[key] = task
        # shield: one caller's cancellation must not cancel the shared fetch.
        result: T = await asyncio.shield(task)
        return result


class StaticMarketData(MarketDataClient):
    """Market data held in memory, for offline runs, evals and tests.

    Applies the same point-in-time filters as the network providers.
    """

    provider = "fixtures"

    def __init__(
        self,
        *,
        prices: Mapping[str, Sequence[Price]] | None = None,
        metrics: Mapping[str, Sequence[FinancialMetrics]] | None = None,
        line_items: Mapping[str, Sequence[LineItem]] | None = None,
        insider_trades: Mapping[str, Sequence[InsiderTrade]] | None = None,
        news: Mapping[str, Sequence[CompanyNews]] | None = None,
        market_caps: Mapping[str, float] | None = None,
    ) -> None:
        self._prices = {key.upper(): list(value) for key, value in (prices or {}).items()}
        self._metrics = {key.upper(): list(value) for key, value in (metrics or {}).items()}
        self._items = {key.upper(): list(value) for key, value in (line_items or {}).items()}
        self._trades = {key.upper(): list(value) for key, value in (insider_trades or {}).items()}
        self._news = {key.upper(): list(value) for key, value in (news or {}).items()}
        self._caps = {key.upper(): value for key, value in (market_caps or {}).items()}

    @property
    def tickers(self) -> list[str]:
        known = {*self._prices, *self._metrics, *self._items, *self._trades, *self._news}
        return sorted(known | set(self._caps))

    async def prices(self, ticker: str, start: date, end: date) -> list[Price]:
        return [bar for bar in prices_until(self._prices.get(ticker, ()), end) if bar.day >= start]

    async def financial_metrics(
        self, ticker: str, as_of: date, *, period: str = "ttm", limit: int = 10
    ) -> list[FinancialMetrics]:
        known = [item for item in self._metrics.get(ticker, ()) if item.period == period]
        return latest_first(known or self._metrics.get(ticker, ()), as_of)[:limit]

    async def line_items(
        self,
        ticker: str,
        names: Sequence[str],
        as_of: date,
        *,
        period: str = "annual",
        limit: int = 10,
    ) -> list[LineItem]:
        known = [item for item in self._items.get(ticker, ()) if item.period == period]
        return latest_first(known or self._items.get(ticker, ()), as_of)[:limit]

    async def insider_trades(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 1000
    ) -> list[InsiderTrade]:
        trades = [
            trade
            for trade in self._trades.get(ticker, ())
            if trade.filing_date <= as_of and (start is None or trade.filing_date >= start)
        ]
        return sorted(trades, key=lambda trade: trade.filing_date, reverse=True)[:limit]

    async def company_news(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 100
    ) -> list[CompanyNews]:
        items = [
            item
            for item in self._news.get(ticker, ())
            if item.date.date() <= as_of and (start is None or item.date.date() >= start)
        ]
        return sorted(items, key=lambda item: item.date, reverse=True)[:limit]

    async def market_cap(self, ticker: str, as_of: date) -> float | None:
        if ticker in self._caps:
            return self._caps[ticker]
        metrics = await self.financial_metrics(ticker, as_of, limit=1)
        return metrics[0].market_cap if metrics else None


_FIXTURE_FILES: dict[str, tuple[str, str, type[BaseModel]]] = {
    "prices": ("prices.json", "prices", Price),
    "metrics": ("financial_metrics.json", "financial_metrics", FinancialMetrics),
    "line_items": ("line_items.json", "search_results", LineItem),
    "insider_trades": ("insider_trades.json", "insider_trades", InsiderTrade),
    "news": ("news.json", "news", CompanyNews),
}


def load_market_fixtures(directory: Path) -> StaticMarketData:
    """Read ``<directory>/<TICKER>/{prices,financial_metrics,line_items,insider_trades,news}.json``.

    Each file is either a list or the provider envelope (``{"prices": [...]}`` and so on, as
    Financial Datasets returns it). ``market_cap.json`` holds a bare number. Missing files are
    simply empty; a malformed one raises :class:`IntegrationError` naming the file.
    """
    if not directory.is_dir():
        raise IntegrationError(f"market fixtures directory not found: {directory}")
    loaded: dict[str, dict[str, list[Any]]] = {key: {} for key in _FIXTURE_FILES}
    caps: dict[str, float] = {}
    for folder in sorted(path for path in directory.iterdir() if path.is_dir()):
        ticker = folder.name.upper()
        for key, (filename, envelope, model) in _FIXTURE_FILES.items():
            path = folder / filename
            if not path.is_file():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                rows = raw.get(envelope, []) if isinstance(raw, dict) else raw
                loaded[key][ticker] = [
                    model.model_validate({"ticker": ticker, **row}) for row in rows
                ]
            except (ValueError, TypeError, ValidationError, AttributeError) as exc:
                raise IntegrationError(
                    f"malformed market fixture {path.name} for {ticker}"
                ) from exc
        cap_path = folder / "market_cap.json"
        if cap_path.is_file():
            value = json.loads(cap_path.read_text(encoding="utf-8"))
            if isinstance(value, int | float) and not isinstance(value, bool):
                caps[ticker] = float(value)
    return StaticMarketData(
        prices=loaded["prices"],
        metrics=loaded["metrics"],
        line_items=loaded["line_items"],
        insider_trades=loaded["insider_trades"],
        news=loaded["news"],
        market_caps=caps,
    )
